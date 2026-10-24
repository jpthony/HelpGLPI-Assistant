"""Authentification.

Trois modes :
- kerberos     : SPNEGO (Negotiate) géré par l’application avec un keytab (KRB5_KTNAME).
- proxy_header : un reverse proxy (Apache mod_auth_gssapi, etc.) authentifie et transmet
                 le login dans un en-tête. À n’utiliser que si le port de l’app n’est pas
                 joignable directement.
- dev          : utilisateur fixe, pour les tests uniquement.

Une fois l’utilisateur identifié, un cookie signé évite de renégocier à chaque appel.
"""
from __future__ import annotations

import base64
import html
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import secret_key, store

COOKIE_NAME = "ga_session"
_serializer = URLSafeTimedSerializer(secret_key(), salt="glpi-assistant-session")


@dataclass
class Principal:
    login: str
    principal: str

    @property
    def is_admin(self) -> bool:
        admins = [a.strip().lower() for a in store.section("auth").get("admin_users", [])]
        return self.login.lower() in admins


def normalize_login(principal: str) -> str:
    cfg = store.section("auth")
    login = principal.strip()
    if "\\" in login:  # DOMAINE\login
        login = login.split("\\", 1)[1]
    if cfg.get("strip_realm", True) and "@" in login:
        login = login.split("@", 1)[0]
    if cfg.get("lowercase", True):
        login = login.lower()
    return login


# --------------------------------------------------------------------- Kerberos
class KerberosError(Exception):
    pass


def kerberos_accept(token_b64: str) -> tuple[str, bytes | None]:
    """Valide un jeton SPNEGO. Retourne (principal, jeton de réponse éventuel)."""
    try:
        import gssapi  # import tardif : le mode dev fonctionne sans la lib
    except ImportError as exc:  # pragma: no cover
        raise KerberosError("La bibliothèque python-gssapi n’est pas installée.") from exc

    spn = store.section("auth").get("service_principal") or ""
    try:
        creds = None
        if spn:
            name = gssapi.Name(spn, gssapi.NameType.hostbased_service)
            creds = gssapi.Credentials(name=name, usage="accept")
        ctx = gssapi.SecurityContext(creds=creds, usage="accept")
        out = ctx.step(base64.b64decode(token_b64))
        if not ctx.complete:
            raise KerberosError("Négociation incomplète (échange multi-étapes non supporté).")
        return str(ctx.initiator_name), out
    except gssapi.exceptions.GSSError as exc:
        raise KerberosError(str(exc)) from exc


# ---------------------------------------------------------------------- Session
def make_session_cookie(principal: Principal) -> str:
    return _serializer.dumps({"l": principal.login, "p": principal.principal, "t": int(time.time())})


def read_session(request: Request) -> Principal | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    max_age = int(store.section("auth").get("session_hours", 12)) * 3600
    try:
        data = _serializer.loads(raw, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return Principal(login=data["l"], principal=data.get("p", data["l"]))


def current_principal(request: Request) -> Principal | None:
    cfg = store.section("auth")
    mode = cfg.get("mode", "kerberos")
    if mode == "dev":
        user = cfg.get("dev_user") or ""
        return Principal(login=normalize_login(user), principal=user) if user else None
    if mode == "proxy_header":
        header = cfg.get("proxy_header") or "X-Remote-User"
        val = request.headers.get(header)
        if val:
            return Principal(login=normalize_login(val), principal=val)
        return None
    return read_session(request)


def require_user(request: Request) -> Principal:
    p = current_principal(request)
    if not p:
        raise HTTPException(status_code=401, detail={"code": "auth_required", "sso": "/auth/sso"})
    return p


def require_admin(request: Request) -> Principal:
    p = require_user(request)
    if not p.is_admin:
        raise HTTPException(status_code=403, detail={"code": "forbidden",
                                                      "message": "Accès réservé aux administrateurs."})
    return p


def safe_next(target: str | None) -> str:
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


# ------------------------------------------------------------- Pages d’erreur SSO
def sso_page(title: str, message: str, hint: str = "") -> str:
    ui = store.section("ui")
    contact = ui.get("support_contact") or ""
    contact_html = f"<p class='c'>Besoin d’aide tout de suite : <strong>{html.escape(contact)}</strong></p>" if contact else ""
    hint_html = f"<details><summary>Détails pour le support</summary><pre>{html.escape(hint)}</pre></details>" if hint else ""
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(ui.get('app_name', 'Assistance'))}</title>
<link rel="stylesheet" href="/assets/css/app.css"></head>
<body class="sso-page"><main class="sso-card">
<h1>{html.escape(title)}</h1><p>{message}</p>{contact_html}{hint_html}
<p><a class="btn btn-primary" href="/auth/sso">Réessayer</a></p>
</main></body></html>"""
