"""Accès à GLPI.

Deux adaptateurs exposent la même interface :

- LegacyGlpi : API REST historique `apirest.php` (App-Token + user_token d'un compte de service).
  Disponible de GLPI 9.5 à GLPI 11 (sur GLPI 11 elle doit être activée dans Configuration > API).
  C'est le mode recommandé : il couvre tickets, demandeur et pièces jointes sur les deux versions.

- V2Glpi : API « haut niveau » de GLPI 11 (`api.php`, OAuth2 grant password, scope api).
  Tickets, demandeur et catégories. Les pièces jointes passent par l'API legacy si ses jetons
  sont aussi renseignés (mode hybride), car le téléversement v2 n'est pas encore stabilisé.

Le ticket est créé par le compte de service ; le demandeur est l'utilisateur SSO.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import store


class GlpiError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass
class GlpiUser:
    id: int
    login: str
    firstname: str = ""
    realname: str = ""
    phone: str = ""
    mobile: str = ""
    location_id: int | None = None
    location: str = ""
    entity_id: int | None = None

    @property
    def display_name(self) -> str:
        full = f"{self.firstname} {self.realname}".strip()
        return full or self.login


@dataclass
class Attachment:
    filename: str
    content: bytes
    mime: str = "application/octet-stream"


@dataclass
class TicketInput:
    title: str
    content_html: str
    type: int  # 1 incident, 2 demande
    urgency: int
    category_id: int | None
    requester: GlpiUser | None
    entity_id: int | None = None
    location_id: int | None = None
    request_type_id: int | None = None
    attachments: list[Attachment] = field(default_factory=list)


def _verify(cfg: dict) -> Any:
    if not cfg.get("verify_tls", True):
        return False
    bundle = cfg.get("ca_bundle")
    if bundle:
        import os
        if os.path.exists(bundle):
            return bundle
    return True


def _err_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code} : {resp.text[:300]}"
    if isinstance(data, list) and data:  # legacy : ["ERROR_CODE", "message"]
        return " — ".join(str(x) for x in data[:2])
    if isinstance(data, dict):
        return str(data.get("detail") or data.get("title") or data.get("message") or data)[:400]
    return str(data)[:400]


# ======================================================================= Legacy
class LegacyGlpi:
    api_name = "API REST legacy (apirest.php)"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.base = cfg["url"].rstrip("/") + "/apirest.php"
        self._session: str | None = None
        self._lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(verify=_verify(self.cfg), timeout=self.cfg.get("timeout", 20))

    def _headers(self, session: str | None = None) -> dict:
        h = {"Content-Type": "application/json"}
        if self.cfg.get("app_token"):
            h["App-Token"] = self.cfg["app_token"]
        if session:
            h["Session-Token"] = session
        return h

    async def _init_session(self, client: httpx.AsyncClient) -> str:
        if not self.cfg.get("user_token"):
            raise GlpiError("Jeton API du compte de service (user_token) manquant.")
        h = self._headers()
        h["Authorization"] = f"user_token {self.cfg['user_token']}"
        r = await client.get(f"{self.base}/initSession", headers=h, params={"get_full_session": "false"})
        if r.status_code != 200:
            raise GlpiError(f"Ouverture de session GLPI refusée : {_err_message(r)}", r.status_code)
        return r.json()["session_token"]

    async def _request(self, method: str, path: str, *, retry: bool = True, **kw) -> httpx.Response:
        async with self._client() as client:
            async with self._lock:
                if not self._session:
                    self._session = await self._init_session(client)
                session = self._session
            extra = kw.pop("headers", {})
            headers = self._headers(session)
            if "files" in kw:
                headers.pop("Content-Type", None)
            headers.update(extra)
            r = await client.request(method, f"{self.base}{path}", headers=headers, **kw)
            if r.status_code == 401 and retry:
                # Session expirée côté GLPI : on en rouvre une et on rejoue la requête une fois.
                self._session = None
                return await self._request(method, path, retry=False, headers=extra, **kw)
            return r

    # ---- interface
    async def test(self) -> dict:
        info: dict[str, Any] = {"api": self.api_name}
        r = await self._request("GET", "/getFullSession")
        if r.status_code != 200:
            raise GlpiError(_err_message(r), r.status_code)
        sess = r.json().get("session", {})
        info["service_account"] = sess.get("glpiname")
        info["profile"] = (sess.get("glpiactiveprofile") or {}).get("name")
        r = await self._request("GET", "/getGlpiConfig")
        if r.status_code == 200:
            info["version"] = r.json().get("cfg_glpi", {}).get("version")
        return info

    async def find_user(self, login: str) -> GlpiUser | None:
        r = await self._request("GET", "/User", params={
            "searchText[name]": login, "range": "0-50", "expand_dropdowns": "true"})
        if r.status_code not in (200, 206):
            raise GlpiError(f"Recherche utilisateur impossible : {_err_message(r)}", r.status_code)
        rows = r.json() if isinstance(r.json(), list) else []
        match = next((u for u in rows if str(u.get("name", "")).lower() == login.lower()), None)
        if not match:
            return None
        # expand_dropdowns remplace locations_id par son libellé : on relit l'id brut.
        loc_name = match.get("locations_id") if isinstance(match.get("locations_id"), str) else ""
        raw = await self._request("GET", f"/User/{match['id']}")
        rawj = raw.json() if raw.status_code == 200 else {}
        loc_id = rawj.get("locations_id") or None
        return GlpiUser(
            id=int(match["id"]), login=match.get("name", login),
            firstname=match.get("firstname") or "", realname=match.get("realname") or "",
            phone=match.get("phone") or "", mobile=match.get("mobile") or "",
            location_id=int(loc_id) if loc_id else None,
            location=loc_name if loc_name and loc_name != "0" else "",
            entity_id=rawj.get("entities_id"),
        )

    async def categories(self) -> list[dict]:
        r = await self._request("GET", "/ITILCategory", params={"range": "0-1999"})
        if r.status_code not in (200, 206):
            raise GlpiError(f"Lecture des catégories impossible : {_err_message(r)}", r.status_code)
        out = []
        for c in r.json() or []:
            out.append({
                "id": int(c["id"]),
                "name": c.get("completename") or c.get("name"),
                "is_incident": bool(c.get("is_incident", 1)),
                "is_request": bool(c.get("is_request", 1)),
                "helpdesk": bool(c.get("is_helpdeskvisible", 1)),
            })
        return sorted(out, key=lambda c: c["name"] or "")

    async def create_ticket(self, t: TicketInput) -> tuple[int, list[str]]:
        """Retourne (id du ticket, avertissements). Une erreur sur les PJ ne fait pas perdre le ticket."""
        data: dict[str, Any] = {
            "name": t.title,
            "content": t.content_html,
            "type": t.type,
            "urgency": t.urgency,
        }
        if t.category_id:
            data["itilcategories_id"] = t.category_id
        if t.requester:
            data["_users_id_requester"] = t.requester.id
        if t.entity_id is not None:
            data["entities_id"] = t.entity_id
        if t.location_id:
            data["locations_id"] = t.location_id
        if t.request_type_id:
            data["requesttypes_id"] = t.request_type_id
        r = await self._request("POST", "/Ticket", json={"input": data})
        if r.status_code not in (200, 201):
            raise GlpiError(f"Création du ticket refusée : {_err_message(r)}", r.status_code)
        body = r.json()
        ticket_id = int(body["id"] if isinstance(body, dict) else body[0]["id"])
        return ticket_id, await self.attach(ticket_id, t.attachments, t.entity_id)

    async def attach(self, ticket_id: int, files: list[Attachment], entity_id: int | None = None) -> list[str]:
        """Téléverse les fichiers et les lie au ticket (Document + Document_Item)."""
        errors = []
        for f in files:
            manifest: dict[str, Any] = {"input": {
                "name": f.filename, "_filename": [f.filename],
                "itemtype": "Ticket", "items_id": ticket_id}}
            if entity_id is not None:
                manifest["input"]["entities_id"] = entity_id
            r = await self._request("POST", "/Document", files=[
                ("uploadManifest", (None, json.dumps(manifest), "application/json")),
                ("filename[0]", (f.filename, f.content, f.mime)),
            ])
            if r.status_code not in (200, 201):
                errors.append(f"pièce jointe « {f.filename} » refusée : {_err_message(r)}")
        return errors


# =========================================================================== v2
class V2Glpi:
    api_name = "API haut niveau v2 (GLPI 11)"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        root = cfg["url"].rstrip("/") + "/api.php"
        ver = (cfg.get("v2_version") or "").strip("/")
        self.root = root
        self.base = f"{root}/{ver}" if ver else root
        self._token: str | None = None
        self._exp = 0.0
        self._lock = asyncio.Lock()
        self._legacy = LegacyGlpi(cfg) if cfg.get("user_token") else None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(verify=_verify(self.cfg), timeout=self.cfg.get("timeout", 20))

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        async with self._lock:
            if self._token and time.time() < self._exp - 60:
                return self._token
            for k in ("v2_client_id", "v2_client_secret", "v2_username", "v2_password"):
                if not self.cfg.get(k):
                    raise GlpiError(f"Paramètre OAuth manquant : {k}")
            r = await client.post(f"{self.root}/token", data={
                "grant_type": "password", "client_id": self.cfg["v2_client_id"],
                "client_secret": self.cfg["v2_client_secret"], "username": self.cfg["v2_username"],
                "password": self.cfg["v2_password"], "scope": "api"})
            if r.status_code != 200:
                raise GlpiError(f"Jeton OAuth refusé : {_err_message(r)}", r.status_code)
            j = r.json()
            self._token = j["access_token"]
            self._exp = time.time() + int(j.get("expires_in", 3600))
            return self._token

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        async with self._client() as client:
            token = await self._get_token(client)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            headers.update(kw.pop("headers", {}))
            return await client.request(method, f"{self.base}{path}", headers=headers, **kw)

    async def test(self) -> dict:
        info: dict[str, Any] = {"api": self.api_name}
        r = await self._request("GET", "/Administration/User/Me")
        if r.status_code != 200:
            raise GlpiError(_err_message(r), r.status_code)
        info["service_account"] = r.json().get("username")
        r = await self._request("GET", "/status")
        if r.status_code == 200:
            info["status"] = "ok"
        info["attachments"] = ("via API legacy" if self._legacy
                               else "indisponibles : renseignez aussi App-Token et user_token")
        return info

    async def find_user(self, login: str) -> GlpiUser | None:
        r = await self._request("GET", "/Administration/User", params={"filter": f"username=={login}", "limit": 5})
        if r.status_code not in (200, 206):
            raise GlpiError(f"Recherche utilisateur impossible : {_err_message(r)}", r.status_code)
        rows = r.json() or []
        m = next((u for u in rows if str(u.get("username", "")).lower() == login.lower()), None)
        if not m:
            return None
        loc = m.get("location") or {}
        ent = m.get("default_entity") or m.get("entity") or {}
        return GlpiUser(
            id=int(m["id"]), login=m.get("username", login),
            firstname=m.get("firstname") or "", realname=m.get("realname") or "",
            phone=m.get("phone") or "", mobile=m.get("mobile") or "",
            location_id=loc.get("id") or None, location=loc.get("name") or "",
            entity_id=ent.get("id") if isinstance(ent, dict) else None,
        )

    async def categories(self) -> list[dict]:
        out, start = [], 0
        while True:
            r = await self._request("GET", "/Dropdowns/ITILCategory", params={"start": start, "limit": 500})
            if r.status_code not in (200, 206):
                raise GlpiError(f"Lecture des catégories impossible : {_err_message(r)}", r.status_code)
            rows = r.json() or []
            for c in rows:
                out.append({
                    "id": int(c["id"]),
                    "name": c.get("completename") or c.get("name"),
                    "is_incident": bool(c.get("is_incident", True)),
                    "is_request": bool(c.get("is_request", True)),
                    "helpdesk": bool(c.get("is_helpdesk_visible", c.get("is_helpdeskvisible", True))),
                })
            if len(rows) < 500:
                break
            start += 500
        return sorted(out, key=lambda c: c["name"] or "")

    async def create_ticket(self, t: TicketInput) -> tuple[int, list[str]]:
        data: dict[str, Any] = {"name": t.title, "content": t.content_html, "type": t.type, "urgency": t.urgency}
        if t.category_id:
            data["category"] = {"id": t.category_id}
        if t.entity_id is not None:
            data["entity"] = {"id": t.entity_id}
        if t.location_id:
            data["location"] = {"id": t.location_id}
        if t.request_type_id:
            data["request_type"] = {"id": t.request_type_id}
        r = await self._request("POST", "/Assistance/Ticket", json=data)
        if r.status_code not in (200, 201):
            raise GlpiError(f"Création du ticket refusée : {_err_message(r)}", r.status_code)
        try:
            ticket_id = int(r.json()["id"])
        except (ValueError, KeyError, TypeError):
            ticket_id = int(r.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1])

        warnings: list[str] = []
        if t.requester:
            warnings += await self._set_requester(ticket_id, t.requester.id)
        if t.attachments:
            if self._legacy:
                warnings += await self._legacy.attach(ticket_id, t.attachments, t.entity_id)
            else:
                warnings.append("pièces jointes non envoyées : l'API v2 nécessite aussi les jetons legacy")
        return ticket_id, warnings

    async def _set_requester(self, ticket_id: int, user_id: int) -> list[str]:
        path = f"/Assistance/Ticket/{ticket_id}/TeamMember"
        r = await self._request("POST", path, json={"type": "User", "id": user_id, "role": "requester"})
        if r.status_code not in (200, 201):
            return [f"demandeur non défini : {_err_message(r)}"]
        # Retire le compte de service ajouté automatiquement comme demandeur (au mieux).
        try:
            team = (await self._request("GET", path)).json() or []
            for m in team:
                if m.get("role") == "requester" and m.get("type") == "User" and int(m.get("id", 0)) != user_id:
                    await self._request("DELETE", path, json={"type": "User", "id": m["id"], "role": "requester"})
        except Exception:  # noqa: BLE001 - nettoyage facultatif
            pass
        return []


# ====================================================================== Factory
_instances: dict[str, LegacyGlpi | V2Glpi] = {}


def get_client() -> LegacyGlpi | V2Glpi:
    """Client partagé (la session/le jeton GLPI est réutilisé) tant que la config ne change pas."""
    cfg = store.section("glpi")
    key = json.dumps(cfg, sort_keys=True, default=str)
    if key in _instances:
        return _instances[key]
    mode = cfg.get("api_mode", "auto")
    if mode == "v1":
        client: LegacyGlpi | V2Glpi = LegacyGlpi(cfg)
    elif mode == "v2":
        client = V2Glpi(cfg)
    # auto : legacy si ses jetons sont présents (couvre GLPI 10 et 11), sinon v2.
    elif cfg.get("user_token"):
        client = LegacyGlpi(cfg)
    elif cfg.get("v2_client_id"):
        client = V2Glpi(cfg)
    else:
        client = LegacyGlpi(cfg)
    _instances.clear()
    _instances[key] = client
    return client


# --------------------------------------------------------------- Petits caches
_cache: dict[str, tuple[float, Any]] = {}


async def cached(key: str, ttl: int, loader):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = await loader()
    _cache[key] = (time.time(), val)
    return val


def clear_cache() -> None:
    _cache.clear()
