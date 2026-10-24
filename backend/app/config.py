"""Configuration persistante de l’assistant.

La configuration vit dans DATA_DIR/config.json. Les valeurs par défaut ci-dessous
sont fusionnées avec le fichier, puis avec quelques variables d’environnement de
démarrage (utiles pour le premier lancement, avant d’avoir accès à l’admin).
"""
from __future__ import annotations

import copy
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
SECRET_FILE = DATA_DIR / ".secret_key"

MASK = "••••••••"

# Chemins des champs secrets : jamais renvoyés en clair à l’interface d’admin.
SECRET_PATHS = [
    ("glpi", "app_token"),
    ("glpi", "user_token"),
    ("glpi", "v2_client_secret"),
    ("glpi", "v2_password"),
    ("llm", "api_key"),
]

DEFAULT_SYSTEM_PROMPT = """Tu es l’assistant du support informatique de Mon Entreprise.
Tu aides des collègues, souvent peu à l’aise avec l’informatique, à signaler un problème ou à formuler une demande.

Ton ton :
- chaleureux, simple, rassurant, jamais condescendant ;
- tu vouvoies ;
- aucun jargon technique : si un terme est nécessaire, explique-le en quelques mots ;
- phrases courtes.

Ta mission : comprendre la situation assez bien pour qu’un technicien puisse agir sans avoir à rappeler l’utilisateur, en posant le moins de questions possible."""

DEFAULT_RULES = """Informations indispensables selon le cas :
- Toujours : ce qui ne fonctionne pas, depuis quand, et si la personne est bloquée dans son travail.
- Message d’erreur : le texte exact ou une capture d’écran (suggérer Windows + Maj + S puis Ctrl + V dans la fenêtre).
- Imprimante : laquelle (nom ou emplacement) et si d’autres collègues sont touchés.
- Messagerie / Outlook / Teams : sur l’ordinateur, le téléphone ou les deux.
- Logiciel métier (M3, etc.) : quel écran ou programme, ce que la personne essayait de faire.
- Réseau / Internet : un seul poste ou plusieurs, câble ou wifi.
- Accès / mot de passe : quel compte ou quelle application. Ne jamais demander de mot de passe.
- Demande de matériel ou de logiciel : pour qui, pour quand, et pourquoi en une phrase."""

DEFAULTS: dict[str, Any] = {
    "ui": {
        "app_name": "Assistance informatique",
        "company": "Mon Entreprise",
        "welcome_title": "Bonjour {prenom},",
        "welcome_subtitle": "qu’est-ce qui vous embête ?",
        "welcome_text": "Racontez-moi avec vos mots, comme vous le diriez à un collègue. Je vous poserai une ou deux questions si besoin, puis je rédige le ticket pour le support.",
        "accent": "#1E5E58",
        "logo_url": "",
        "support_contact": "",
        "glpi_ticket_url": "https://support.local.lan/front/ticket.form.php?id={id}",
        "quick_topics": [
            {"emoji": "💻", "label": "Mon ordinateur", "prompt": "J’ai un problème avec mon ordinateur : "},
            {"emoji": "🖨️", "label": "Une imprimante", "prompt": "J’ai un problème d’impression : "},
            {"emoji": "✉️", "label": "Messagerie, Teams", "prompt": "J’ai un souci avec ma messagerie : "},
            {"emoji": "📦", "label": "M3 ou logiciel métier", "prompt": "J’ai un problème dans M3 : "},
            {"emoji": "🔑", "label": "Accès, mot de passe", "prompt": "Je n’arrive pas à accéder à "},
            {"emoji": "🛒", "label": "Demander du matériel", "prompt": "J’aurais besoin de "},
        ],
    },
    "auth": {
        # kerberos | proxy_header | dev
        "mode": "kerberos",
        # Laisser vide pour accepter n’importe quel principal présent dans le keytab.
        # Sinon, forme "HTTP@assistance.local.lan".
        "service_principal": "",
        "strip_realm": True,
        "lowercase": True,
        "session_hours": 12,
        "admin_users": [],
        "proxy_header": "X-Remote-User",
        "dev_user": "",
    },
    "glpi": {
        # auto | v1 (apirest.php, GLPI 9.5 → 11) | v2 (API haut niveau GLPI 11)
        "api_mode": "auto",
        "url": "https://support.local.lan",
        "app_token": "",
        "user_token": "",
        "v2_client_id": "",
        "v2_client_secret": "",
        "v2_username": "",
        "v2_password": "",
        "v2_version": "",
        "verify_tls": True,
        "ca_bundle": "/certs/ca.pem",
        "timeout": 20,
        # null = entité par défaut du demandeur, sinon identifiant fixe.
        "entity_id": None,
        # Compte GLPI utilisé comme demandeur si l’utilisateur SSO n’existe pas dans GLPI.
        "fallback_requester_id": None,
        # Source de la demande (requesttypes_id), ex. « Helpdesk ». null = défaut GLPI.
        "request_type_id": None,
        "use_user_location": True,
        "only_helpdesk_categories": True,
        "title_prefix": "",
    },
    "llm": {
        # openai (compatible : vLLM, LM Studio, llama.cpp, Ollama /v1…) | ollama (API native)
        "provider": "openai",
        "base_url": "https://llm.local.lan/v1",
        "api_key": "",
        "model": "",
        "temperature": 0.2,
        "max_tokens": 1500,
        "timeout": 90,
        "json_mode": True,
        "verify_tls": True,
    },
    "agent": {
        "max_rounds": 2,
        "max_questions": 3,
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "rules": DEFAULT_RULES,
        "suggest_category": True,
        "allowed_category_ids": [],
        "include_transcript": True,
        "footer_note": "Ticket rédigé avec l’assistant de saisie.",
    },
    "attachments": {
        "max_files": 6,
        "max_mb": 10,
        "extensions": ["png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "log", "csv",
                       "docx", "xlsx", "pptx", "msg", "eml", "zip"],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_bootstrap(cfg: dict) -> dict:
    """Variables d’environnement de démarrage (prioritaires sur le fichier si définies)."""
    env_map = {
        "AUTH_MODE": ("auth", "mode"),
        "DEV_USER": ("auth", "dev_user"),
        "KRB_SERVICE_PRINCIPAL": ("auth", "service_principal"),
        "GLPI_URL": ("glpi", "url"),
        "GLPI_APP_TOKEN": ("glpi", "app_token"),
        "GLPI_USER_TOKEN": ("glpi", "user_token"),
        "LLM_BASE_URL": ("llm", "base_url"),
        "LLM_API_KEY": ("llm", "api_key"),
        "LLM_MODEL": ("llm", "model"),
        "LLM_PROVIDER": ("llm", "provider"),
    }
    for env, (sec, key) in env_map.items():
        val = os.environ.get(env)
        if val:
            cfg[sec][key] = val
    admins = os.environ.get("ADMIN_USERS")
    if admins:
        extra = [a.strip().lower() for a in admins.split(",") if a.strip()]
        cfg["auth"]["admin_users"] = sorted(set(cfg["auth"]["admin_users"]) | set(extra))
    return cfg


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cfg = self._load()

    def _load(self) -> dict:
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                data = {}
        return _env_bootstrap(_deep_merge(DEFAULTS, data))

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._cfg)

    def section(self, name: str) -> dict:
        return self.get()[name]

    def masked(self) -> dict:
        cfg = self.get()
        for sec, key in SECRET_PATHS:
            if cfg[sec].get(key):
                cfg[sec][key] = MASK
        return cfg

    def update(self, incoming: dict) -> dict:
        """Fusionne une config envoyée par l’admin. Un secret égal au masque est conservé."""
        with self._lock:
            current = copy.deepcopy(self._cfg)
            for sec, key in SECRET_PATHS:
                if incoming.get(sec, {}).get(key) == MASK:
                    incoming[sec][key] = current[sec].get(key, "")
            merged = _deep_merge(current, {k: v for k, v in incoming.items() if k in DEFAULTS})
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), "utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(CONFIG_FILE)
            self._cfg = _env_bootstrap(merged)
            return copy.deepcopy(self._cfg)


def secret_key() -> str:
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    key = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(key)
    os.chmod(SECRET_FILE, 0o600)
    return key


store = ConfigStore()
