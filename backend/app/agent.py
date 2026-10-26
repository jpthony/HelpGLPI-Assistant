"""Agent de qualification de tickets.

À chaque tour, le serveur envoie au LLM le dossier complet (description, échanges,
pièces jointes, profil GLPI, catégories autorisées) : l’agent est sans état, ce qui
le rend robuste avec des modèles locaux modestes et facile à déboguer.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import store

TECH_CONTRACT = """## Format de réponse (obligatoire)
Tu réponds UNIQUEMENT par un objet JSON valide, sans texte autour ni balises Markdown :
{
  "status": "need_info" ou "ready",
  "message": "1 à 2 phrases courtes adressées à l’utilisateur, en le vouvoyant",
  "questions": [
    {"id": "q1", "text": "une question simple, une seule idée", "options": ["choix court", "autre choix"], "multiple": false}
  ],
  "ticket": {
    "title": "titre factuel, 70 caractères maximum, sans le nom de l’utilisateur",
    "summary": "2 à 4 phrases à la troisième personne, destinées au technicien",
    "facts": [{"label": "Appareil concerné", "value": "ordinateur portable"}],
    "type": "incident" ou "demande",
    "urgency": 1 à 5,
    "category_id": un identifiant de la liste des catégories, ou null
  }
}

## Règles
- status = "need_info" seulement s’il manque une information INDISPENSABLE. Sinon "ready".
- Au plus {max_questions} questions par tour, les plus utiles d’abord. Aucune question si status = "ready".
- Propose 2 à 5 "options" courtes quand les réponses probables sont prévisibles ; liste vide pour une réponse libre.
  L’interface ajoute toujours « Je ne sais pas » et un champ libre : ne les ajoute pas toi-même.
- "multiple": true uniquement si plusieurs réponses peuvent être vraies en même temps.
- Ne redemande jamais une information déjà donnée, ni une information du profil utilisateur.
- Si l’utilisateur a répondu « Je ne sais pas », n’insiste pas : avance avec ce que tu as.
- Ne demande jamais de mot de passe ni de code.
- "ticket" est TOUJOURS rempli avec ta compréhension actuelle, même si status = "need_info".
  N’invente rien : un fait non fourni n’apparaît pas dans "facts".
- "facts" : 2 à 8 éléments factuels utiles au technicien (appareil, logiciel, message d’erreur, depuis quand, personnes touchées…).
- Urgence : 1 très basse, 2 basse (ça peut attendre), 3 moyenne (gêne le travail), 4 haute (la personne est bloquée),
  5 très haute (plusieurs personnes ou une activité entière bloquées).
- Type : "incident" si quelque chose ne fonctionne plus comme avant ; "demande" pour un besoin nouveau (accès, matériel, installation).
- Si des captures d’écran sont jointes, ne demande pas le texte de l’erreur : le technicien le verra.
"""


# ============================================================== LLM transport
class LLMError(Exception):
    pass


def _verify(cfg: dict) -> Any:
    if not cfg.get("verify_tls", True):
        return False
    import os
    bundle = store.section("glpi").get("ca_bundle")
    return bundle if bundle and os.path.exists(bundle) else True


async def chat(messages: list[dict], *, json_mode: bool | None = None, cfg: dict | None = None) -> str:
    cfg = cfg or store.section("llm")
    if not cfg.get("model"):
        raise LLMError("Aucun modèle n’est configuré (Administration > Modèle IA).")
    jm = cfg.get("json_mode", True) if json_mode is None else json_mode
    base = cfg["base_url"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    async with httpx.AsyncClient(verify=_verify(cfg), timeout=float(cfg.get("timeout", 90))) as client:
        try:
            if cfg.get("provider") == "ollama":
                body: dict[str, Any] = {
                    "model": cfg["model"], "messages": messages, "stream": False,
                    "options": {"temperature": float(cfg.get("temperature", 0.2)),
                                "num_predict": int(cfg.get("max_tokens", 1500))}}
                if jm:
                    body["format"] = "json"
                r = await client.post(f"{base}/api/chat", json=body, headers=headers)
                if r.status_code != 200:
                    raise LLMError(f"Ollama a répondu HTTP {r.status_code} : {r.text[:300]}")
                return r.json()["message"]["content"]

            body = {"model": cfg["model"], "messages": messages,
                    "temperature": float(cfg.get("temperature", 0.2)),
                    "max_tokens": int(cfg.get("max_tokens", 1500))}
            if jm:
                body["response_format"] = {"type": "json_object"}
            r = await client.post(f"{base}/chat/completions", json=body, headers=headers)
            if r.status_code == 400 and jm:  # serveur qui ne gère pas response_format
                body.pop("response_format", None)
                r = await client.post(f"{base}/chat/completions", json=body, headers=headers)
            if r.status_code != 200:
                raise LLMError(f"Le serveur LLM a répondu HTTP {r.status_code} : {r.text[:300]}")
            return r.json()["choices"][0]["message"]["content"] or ""
        except httpx.TimeoutException as exc:
            raise LLMError(f"Le modèle n’a pas répondu en {cfg.get('timeout', 90)} s.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Serveur LLM injoignable : {exc}") from exc


async def list_models(cfg: dict | None = None) -> list[str]:
    cfg = cfg or store.section("llm")
    base = cfg["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    async with httpx.AsyncClient(verify=_verify(cfg), timeout=15) as client:
        if cfg.get("provider") == "ollama":
            r = await client.get(f"{base}/api/tags", headers=headers)
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        r = await client.get(f"{base}/models", headers=headers)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_json(text: str) -> dict:
    text = _THINK.sub("", text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, depth = text.find("{"), 0
    if start < 0:
        raise ValueError("aucun objet JSON")
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON tronqué")


# ================================================================== Dossier
def build_dossier(*, description: str, exchanges: list[dict], attachments: list[dict],
                  user: dict | None, categories: list[dict], round_no: int, max_rounds: int,
                  force_ready: bool) -> str:
    lines = ["## Profil de l’utilisateur (connu, ne pas redemander)"]
    if user:
        for label, key in (("Nom", "display_name"), ("Lieu", "location"), ("Téléphone", "phone")):
            if user.get(key):
                lines.append(f"- {label} : {user[key]}")
    else:
        lines.append("- (profil inconnu)")

    lines += ["", "## Description initiale de l’utilisateur", description.strip() or "(vide)"]

    if exchanges:
        lines += ["", "## Échanges déjà faits"]
        for ex in exchanges:
            q = (ex.get("q") or "").strip()
            a = (ex.get("a") or "").strip() or "(pas de réponse)"
            lines.append(f"- Question : {q}\n  Réponse : {a}" if q else f"- Précision ajoutée par l’utilisateur : {a}")

    if attachments:
        lines += ["", "## Pièces jointes fournies"]
        for a in attachments:
            kind = "capture d’écran / image" if str(a.get("type", "")).startswith("image/") else "fichier"
            lines.append(f"- {a.get('name')} ({kind})")

    if categories:
        lines += ["", "## Catégories disponibles (id : libellé [usage])"]
        for c in categories[:200]:
            usage = "/".join(u for u, ok in (("incident", c["is_incident"]), ("demande", c["is_request"])) if ok)
            lines.append(f"- {c['id']} : {c['name']} [{usage}]")

    lines += ["", f"## Tour {round_no} sur {max_rounds + 1}"]
    if force_ready:
        lines.append("C’est le dernier tour : status DOIT être \"ready\". Rédige le meilleur ticket possible.")
    return "\n".join(lines)


def _clean_str(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def normalize(raw: dict, *, allowed_ids: set[int], max_questions: int, force_ready: bool,
              description: str) -> dict:
    t = raw.get("ticket") if isinstance(raw.get("ticket"), dict) else {}
    try:
        urgency = min(5, max(1, int(t.get("urgency", 3))))
    except (TypeError, ValueError):
        urgency = 3
    ttype = "demande" if str(t.get("type", "")).lower().startswith(("dem", "req")) else "incident"
    try:
        cat = int(t["category_id"]) if t.get("category_id") not in (None, "", "null") else None
    except (TypeError, ValueError):
        cat = None
    if cat is not None and allowed_ids and cat not in allowed_ids:
        cat = None

    facts = []
    for f in t.get("facts") or []:
        if isinstance(f, dict) and f.get("label") and f.get("value"):
            facts.append({"label": _clean_str(f["label"], 60), "value": _clean_str(f["value"], 300)})

    questions = []
    status = "ready" if force_ready else ("need_info" if raw.get("status") == "need_info" else "ready")
    if status == "need_info":
        for i, q in enumerate(raw.get("questions") or []):
            if not isinstance(q, dict) or not q.get("text"):
                continue
            opts = [_clean_str(o, 80) for o in (q.get("options") or []) if str(o).strip()]
            opts = [o for o in opts if o.lower() not in ("je ne sais pas", "autre", "ne sais pas")][:6]
            questions.append({"id": _clean_str(q.get("id") or f"q{i + 1}", 20), "text": _clean_str(q["text"], 240),
                              "options": opts, "multiple": bool(q.get("multiple"))})
        questions = questions[:max_questions]
        if not questions:
            status = "ready"

    title = _clean_str(t.get("title"), 90) or _clean_str(description, 70) or "Demande d’assistance"
    return {
        "status": status,
        "message": _clean_str(raw.get("message"), 400),
        "questions": questions,
        "ticket": {
            "title": title,
            "summary": str(t.get("summary") or description).strip()[:2000],
            "facts": facts[:10],
            "type": ttype,
            "urgency": urgency,
            "category_id": cat,
        },
    }


async def run_turn(*, description: str, exchanges: list[dict], attachments: list[dict],
                   user: dict | None, categories: list[dict], rounds_done: int,
                   force: bool = False) -> dict:
    acfg = store.section("agent")
    max_rounds = int(acfg.get("max_rounds", 2))
    max_q = int(acfg.get("max_questions", 3))
    force_ready = force or rounds_done >= max_rounds
    allowed = {c["id"] for c in categories}

    system = "\n\n".join(filter(None, [
        acfg.get("system_prompt", "").strip(),
        acfg.get("rules", "").strip(),
        TECH_CONTRACT.replace("{max_questions}", str(max_q)),
    ]))
    dossier = build_dossier(description=description, exchanges=exchanges, attachments=attachments,
                            user=user, categories=categories if acfg.get("suggest_category", True) else [],
                            round_no=rounds_done + 1, max_rounds=max_rounds, force_ready=force_ready)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": dossier}]

    text = await chat(messages)
    try:
        raw = extract_json(text)
    except (ValueError, json.JSONDecodeError):
        messages += [{"role": "assistant", "content": text[:4000]},
                     {"role": "user", "content": "Ta réponse n’était pas un JSON valide. Renvoie uniquement l’objet JSON demandé."}]
        try:
            raw = extract_json(await chat(messages))
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMError(
                "Le modèle n’a pas renvoyé de JSON valide après une relance. "
                "Vérifiez le modèle et son respect des consignes JSON."
            ) from exc

    return normalize(raw, allowed_ids=allowed, max_questions=max_q, force_ready=force_ready,
                     description=description)
