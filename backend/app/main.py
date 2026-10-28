"""Assistant de saisie de tickets GLPI — point d’entrée FastAPI."""
from __future__ import annotations

import html
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import agent, glpi
from .auth import (COOKIE_NAME, KerberosError, Principal, current_principal, kerberos_accept,
                   make_session_cookie, normalize_login, require_admin, require_user, safe_next, sso_page)
from .config import store

log = logging.getLogger("glpi-assistant")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

FRONTEND = Path(os.environ.get("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))
app = FastAPI(title="Assistant tickets GLPI", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

# Journal en mémoire, consultable depuis l’administration.
EVENTS: deque[dict] = deque(maxlen=300)


def event(level: str, kind: str, user: str | None, detail: str) -> None:
    EVENTS.appendleft({"ts": int(time.time()), "level": level, "kind": kind, "user": user, "detail": detail})
    getattr(log, "warning" if level == "warn" else level, log.info)("%s %s %s", kind, user, detail)


# ------------------------------------------------------------------ Middleware
@app.middleware("http")
async def csrf_and_headers(request: Request, call_next):
    # Les appels d’API qui modifient quelque chose doivent venir de notre JS (en-tête personnalisé).
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-requested-with") != "glpi-assistant":
            return JSONResponse({"detail": {"code": "csrf", "message": "Requête refusée."}}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # Revalidation à chaque chargement (304 si inchangé) : pas de CSS/JS périmé après une mise à jour.
    if request.url.path.startswith("/assets/"):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def _cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# ======================================================================= Pages
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    p = current_principal(request)
    if not p:
        return RedirectResponse("/auth/sso?next=/admin", 303)
    if not p.is_admin:
        return RedirectResponse("/", 303)
    return FileResponse(FRONTEND / "admin.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


# ======================================================================== SSO
@app.get("/auth/sso", include_in_schema=False)
async def sso(request: Request, next: str = "/"):
    target = safe_next(next)
    mode = store.section("auth").get("mode")
    if mode != "kerberos":
        return RedirectResponse(target, 303)

    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("negotiate "):
        page = sso_page(
            "Connexion automatique impossible",
            "Votre navigateur n’a pas transmis votre identité Windows. Ouvrez cette page depuis un poste "
            "de l’entreprise, connecté au domaine, avec Edge ou Chrome.",
            "Aucun en-tête Authorization: Negotiate reçu. Vérifiez la stratégie AuthServerAllowlist "
            "(ou la zone Intranet local) pour ce nom d’hôte.")
        return HTMLResponse(page, status_code=401, headers={"WWW-Authenticate": "Negotiate"})

    token = authz.split(" ", 1)[1].strip()
    if token.startswith("TlRMTVNT"):  # le navigateur s’est rabattu sur NTLM
        event("warn", "sso", None, "jeton NTLM reçu au lieu de Kerberos")
        page = sso_page(
            "Connexion automatique impossible",
            "Votre poste a tenté une méthode de connexion non prise en charge.",
            "Jeton NTLM reçu : le SPN HTTP/<nom d’hôte> est probablement absent ou en double dans l’AD, "
            "ou la page est ouverte par une adresse IP au lieu de son nom DNS.")
        return HTMLResponse(page, status_code=401)

    try:
        principal, out = kerberos_accept(token)
    except KerberosError as exc:
        event("error", "sso", None, f"échec Kerberos : {exc}")
        return HTMLResponse(sso_page("Connexion refusée",
                                     "Votre identité n’a pas pu être vérifiée.", str(exc)), status_code=403)

    p = Principal(login=normalize_login(principal), principal=principal)
    event("info", "sso", p.login, f"connexion ({principal})")
    resp = RedirectResponse(target, 303)
    resp.set_cookie(COOKIE_NAME, make_session_cookie(p), httponly=True, samesite="lax",
                    secure=_cookie_secure(request),
                    max_age=int(store.section("auth").get("session_hours", 12)) * 3600)
    if out:
        import base64
        resp.headers["WWW-Authenticate"] = "Negotiate " + base64.b64encode(out).decode()
    return resp


@app.get("/auth/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/", 303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ===================================================================== Helpers
async def _glpi_user(login: str) -> glpi.GlpiUser | None:
    client = glpi.get_client()
    return await glpi.cached(f"user:{login}", 600, lambda: client.find_user(login))


async def _categories() -> list[dict]:
    gcfg, acfg = store.section("glpi"), store.section("agent")
    client = glpi.get_client()
    cats = await glpi.cached("categories", 600, client.categories)
    if gcfg.get("only_helpdesk_categories", True):
        cats = [c for c in cats if c["helpdesk"]]
    allowed = set(acfg.get("allowed_category_ids") or [])
    if allowed:
        cats = [c for c in cats if c["id"] in allowed]
    return cats


def _public_ui() -> dict:
    ui, att, acfg = store.section("ui"), store.section("attachments"), store.section("agent")
    return {**ui, "attachments": att, "max_rounds": acfg.get("max_rounds", 2),
            "suggest_category": acfg.get("suggest_category", True)}


# ==================================================================== API user
@app.get("/api/me")
async def me(request: Request):
    p = current_principal(request)
    if not p:
        mode = store.section("auth").get("mode")
        raise HTTPException(401, detail={"code": "auth_required",
                                         "sso": "/auth/sso" if mode == "kerberos" else None})
    user, warning = None, None
    try:
        user = await _glpi_user(p.login)
        if not user:
            warning = "not_in_glpi"
    except glpi.GlpiError as exc:
        warning = "glpi_unreachable"
        event("error", "glpi", p.login, f"lecture profil : {exc}")
    return {
        "login": p.login,
        "display_name": user.display_name if user else p.login,
        "firstname": (user.firstname if user else "") or "",
        "glpi_user_id": user.id if user else None,
        "is_admin": p.is_admin,
        "warning": warning,
        "ui": _public_ui(),
    }


@app.get("/api/categories")
async def categories(_: Principal = Depends(require_user)):
    try:
        return [{"id": c["id"], "name": c["name"], "is_incident": c["is_incident"],
                 "is_request": c["is_request"]} for c in await _categories()]
    except glpi.GlpiError:
        return []


@app.post("/api/agent/turn")
async def agent_turn(request: Request, p: Principal = Depends(require_user)):
    body = await request.json()
    description = str(body.get("description", "")).strip()
    if len(description) < 3:
        raise HTTPException(422, detail={"code": "empty", "message": "Décrivez votre problème en quelques mots."})
    exchanges = [{"q": str(e.get("q") or "")[:400], "a": str(e.get("a") or "")[:2000]}
                 for e in (body.get("exchanges") or [])][:30]
    attachments = [{"name": str(a.get("name"))[:200], "type": str(a.get("type") or "")}
                   for a in (body.get("attachments") or [])][:20]
    user_ctx, cats = None, []
    try:
        u = await _glpi_user(p.login)
        if u:
            user_ctx = {"display_name": u.display_name, "location": u.location, "phone": u.phone or u.mobile}
        cats = await _categories()
    except glpi.GlpiError as exc:
        event("warn", "glpi", p.login, f"contexte indisponible pour l’agent : {exc}")

    try:
        result = await agent.run_turn(description=description[:6000], exchanges=exchanges,
                                      attachments=attachments, user=user_ctx, categories=cats,
                                      rounds_done=int(body.get("rounds_done", 0)),
                                      force=bool(body.get("force")))
    except agent.LLMError as exc:
        event("error", "llm", p.login, str(exc))
        raise HTTPException(502, detail={"code": "llm", "message":
                                         "L’assistant ne répond pas pour le moment. Vous pouvez rédiger le ticket vous-même ci-contre."})
    event("info", "agent", p.login, f"tour {int(body.get('rounds_done', 0)) + 1} → {result['status']}")
    return result


TYPE_MAP = {"incident": 1, "demande": 2}


def build_content(payload: dict, user: glpi.GlpiUser | None, login: str, attachments: list[str]) -> str:
    acfg = store.section("agent")
    e = html.escape
    parts = [f"<p>{e(payload.get('summary', '')).replace(chr(10), '<br>')}</p>"]
    facts = [f for f in payload.get("facts") or [] if f.get("label") and f.get("value")]
    if facts:
        parts.append("<ul>" + "".join(f"<li><strong>{e(f['label'])}</strong> : {e(f['value'])}</li>"
                                      for f in facts) + "</ul>")
    if attachments:
        parts.append("<p><strong>Pièces jointes</strong> : " + e(", ".join(attachments)) + "</p>")
    if acfg.get("include_transcript", True):
        parts.append("<hr><p><strong>Message d’origine de l’utilisateur</strong></p>"
                     f"<blockquote>{e(payload.get('description', '')).replace(chr(10), '<br>')}</blockquote>")
        ex = payload.get("exchanges") or []
        if ex:
            items = []
            for x in ex:
                q, a = e(x.get("q") or ""), e(x.get("a") or "")
                items.append(f"<li><em>{q}</em><br>{a}</li>" if q else f"<li>Précision : {a}</li>")
            parts.append("<p><strong>Questions de l’assistant</strong></p><ul>" + "".join(items) + "</ul>")
    if not user:
        parts.append(f"<p><strong>Attention</strong> : l’utilisateur « {e(login)} » est introuvable dans GLPI.</p>")
    if acfg.get("footer_note"):
        parts.append(f"<p><small>{e(acfg['footer_note'])}</small></p>")
    return "\n".join(parts)


@app.post("/api/tickets")
async def create_ticket(request: Request, payload: str = Form(...),
                        files: list[UploadFile] = File(default=[]),
                        p: Principal = Depends(require_user)):
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(422, detail={"code": "payload", "message": "Données du ticket illisibles."})
    title = str(data.get("title") or "").strip()[:250]
    if not title:
        raise HTTPException(422, detail={"code": "title", "message": "Donnez un titre au ticket."})

    att_cfg = store.section("attachments")
    exts = {x.lower().lstrip(".") for x in att_cfg.get("extensions", [])}
    max_bytes = int(att_cfg.get("max_mb", 10)) * 1024 * 1024
    files = [f for f in files if f.filename]
    if len(files) > int(att_cfg.get("max_files", 6)):
        raise HTTPException(422, detail={"code": "files", "message": "Trop de pièces jointes."})
    attachments = []
    for f in files:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if exts and ext not in exts:
            raise HTTPException(422, detail={"code": "files", "message": f"Type de fichier non accepté : {f.filename}"})
        content = await f.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(422, detail={"code": "files", "message": f"Fichier trop lourd : {f.filename}"})
        attachments.append(glpi.Attachment(filename=os.path.basename(f.filename), content=content,
                                           mime=f.content_type or "application/octet-stream"))

    gcfg = store.section("glpi")
    try:
        user = await _glpi_user(p.login)
    except glpi.GlpiError as exc:
        event("error", "glpi", p.login, str(exc))
        raise HTTPException(502, detail={"code": "glpi", "message": "Le serveur de tickets est injoignable. Réessayez dans un instant."})

    requester = user
    if not user:
        fb = gcfg.get("fallback_requester_id")
        if not fb:
            event("warn", "ticket", p.login, "utilisateur absent de GLPI, pas de demandeur de repli")
            raise HTTPException(409, detail={"code": "not_in_glpi", "message":
                                             "Votre compte n’existe pas encore dans l’outil de tickets. Contactez le support."})
        requester = glpi.GlpiUser(id=int(fb), login="fallback")

    entity = gcfg.get("entity_id")
    if entity in ("", None) and user and user.entity_id not in (None, ""):
        entity = user.entity_id
    try:
        cat = int(data["category_id"]) if data.get("category_id") else None
    except (TypeError, ValueError):
        cat = None
    try:
        urgency = min(5, max(1, int(data.get("urgency", 3))))
    except (TypeError, ValueError):
        urgency = 3

    prefix = gcfg.get("title_prefix") or ""
    ticket = glpi.TicketInput(
        title=f"{prefix}{title}",
        content_html=build_content(data, user, p.login, [a.filename for a in attachments]),
        type=TYPE_MAP.get(data.get("type"), 1),
        urgency=urgency,
        category_id=cat,
        requester=requester,
        entity_id=int(entity) if entity not in (None, "") else None,
        location_id=user.location_id if (user and gcfg.get("use_user_location", True)) else None,
        request_type_id=gcfg.get("request_type_id") or None,
        attachments=attachments,
    )
    client = glpi.get_client()
    try:
        ticket_id, warnings = await client.create_ticket(ticket)
    except glpi.GlpiError as exc:
        event("error", "ticket", p.login, str(exc))
        raise HTTPException(502, detail={"code": "glpi", "message":
                                         "Le ticket n’a pas pu être enregistré. Vos informations sont conservées : réessayez."})

    url_tpl = store.section("ui").get("glpi_ticket_url") or ""
    for w in warnings:
        event("warn", "ticket", p.login, f"ticket #{ticket_id} : {w}")
    event("info", "ticket", p.login, f"ticket #{ticket_id} créé ({len(attachments)} PJ)")
    return {"id": ticket_id, "url": url_tpl.replace("{id}", str(ticket_id)) if url_tpl else None,
            "warning": "Le ticket est créé, mais certaines pièces jointes n’ont pas pu être ajoutées. "
                       "Envoyez-les au support en rappelant le numéro du ticket." if warnings else None}


# =================================================================== API admin
@app.get("/api/admin/config")
async def admin_get_config(_: Principal = Depends(require_admin)):
    from .config import DEFAULT_RULES, DEFAULT_SYSTEM_PROMPT
    return {"config": store.masked(), "defaults": {"system_prompt": DEFAULT_SYSTEM_PROMPT, "rules": DEFAULT_RULES},
            "env_locked": [k for k in ("AUTH_MODE", "GLPI_URL", "GLPI_APP_TOKEN", "GLPI_USER_TOKEN",
                                        "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_PROVIDER")
                           if os.environ.get(k)]}


@app.put("/api/admin/config")
async def admin_put_config(request: Request, p: Principal = Depends(require_admin)):
    incoming = await request.json()
    # Garde-fou : un admin ne peut pas se retirer lui-même la main.
    admins = [a.strip().lower() for a in incoming.get("auth", {}).get("admin_users", []) if a.strip()]
    if "auth" in incoming:
        incoming["auth"]["admin_users"] = sorted(set(admins) | {p.login.lower()})
    store.update(incoming)
    glpi.clear_cache()
    event("info", "admin", p.login, "configuration enregistrée")
    return {"config": store.masked()}


def _merged_section(name: str, override: dict | None) -> dict:
    """Section courante, éventuellement surchargée par les valeurs non enregistrées du formulaire."""
    from .config import MASK
    sec = store.section(name)
    for k, v in (override or {}).items():
        if v != MASK:
            sec[k] = v
    return sec


@app.post("/api/admin/test/glpi")
async def admin_test_glpi(request: Request, p: Principal = Depends(require_admin)):
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    cfg = _merged_section("glpi", body.get("glpi"))
    mode = cfg.get("api_mode", "auto")
    client = (glpi.V2Glpi(cfg) if mode == "v2" or (mode == "auto" and not cfg.get("user_token") and cfg.get("v2_client_id"))
              else glpi.LegacyGlpi(cfg))
    try:
        info = await client.test()
        cats = await client.categories()
        info["categories"] = len(cats)
        probe = body.get("probe_login") or p.login
        u = await client.find_user(probe)
        info["probe"] = {"login": probe, "found": bool(u), "name": u.display_name if u else None,
                         "id": u.id if u else None, "location": u.location if u else None}
        return {"ok": True, "info": info}
    except glpi.GlpiError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__} : {exc}"}


@app.post("/api/admin/glpi/categories")
async def admin_categories(request: Request, _: Principal = Depends(require_admin)):
    body = await request.json()
    cfg = _merged_section("glpi", body.get("glpi"))
    client = glpi.V2Glpi(cfg) if cfg.get("api_mode") == "v2" else glpi.LegacyGlpi(cfg)
    try:
        return {"ok": True, "categories": await client.categories()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.post("/api/admin/test/llm")
async def admin_test_llm(request: Request, _: Principal = Depends(require_admin)):
    body = await request.json()
    cfg = _merged_section("llm", body.get("llm"))
    out: dict[str, Any] = {}
    try:
        out["models"] = await agent.list_models(cfg)
    except Exception as exc:  # noqa: BLE001
        out["models_error"] = str(exc)
    if not cfg.get("model"):
        return {"ok": bool(out.get("models")), **out, "error": None if out.get("models") else out.get("models_error")}
    t0 = time.time()
    try:
        txt = await agent.chat([{"role": "system", "content": "Réponds uniquement en JSON."},
                                {"role": "user", "content": 'Renvoie {"ok": true, "langue": "fr"}'}], cfg=cfg)
        out["latency_ms"] = int((time.time() - t0) * 1000)
        out["sample"] = txt[:400]
        try:
            agent.extract_json(txt)
            out["json_ok"] = True
        except Exception:  # noqa: BLE001
            out["json_ok"] = False
        return {"ok": True, **out}
    except agent.LLMError as exc:
        return {"ok": False, **out, "error": str(exc)}


@app.post("/api/admin/test/agent")
async def admin_test_agent(request: Request, p: Principal = Depends(require_admin)):
    body = await request.json()
    try:
        cats = await _categories()
    except glpi.GlpiError:
        cats = []
    t0 = time.time()
    try:
        res = await agent.run_turn(description=str(body.get("description", "")), exchanges=[],
                                   attachments=[], user=None, categories=cats, rounds_done=0)
        return {"ok": True, "result": res, "latency_ms": int((time.time() - t0) * 1000)}
    except agent.LLMError as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/admin/events")
async def admin_events(_: Principal = Depends(require_admin)):
    return list(EVENTS)


@app.exception_handler(HTTPException)
async def http_exc(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse({"detail": detail}, status_code=exc.status_code)
