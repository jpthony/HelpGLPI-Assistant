"""Faux GLPI (API legacy) et faux LLM (compatible OpenAI) pour essayer l'interface sans rien brancher.

    pip install fastapi uvicorn python-multipart
    uvicorn tests.mock_services:glpi --port 9001 &
    uvicorn tests.mock_services:llm  --port 9002 &
Puis dans /admin : GLPI = http://127.0.0.1:9001 (jetons : app / user), LLM = http://127.0.0.1:9002/v1, modèle « mock ».
"""
import json

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

glpi = FastAPI()
TICKETS, DOCS = {}, {}
USERS = [{"id": 7, "name": "jpthony", "firstname": "Jean-Pierre", "realname": "Thony", "phone": "1234",
          "mobile": "", "locations_id": 3, "entities_id": 0}]
LOCS = {3: "Monaco > Siège > 2e étage"}
CATS = [
    {"id": 1, "completename": "Matériel > Imprimante", "is_incident": 1, "is_request": 0, "is_helpdeskvisible": 1},
    {"id": 2, "completename": "Matériel > Poste de travail", "is_incident": 1, "is_request": 1, "is_helpdeskvisible": 1},
    {"id": 3, "completename": "Logiciel > M3", "is_incident": 1, "is_request": 1, "is_helpdeskvisible": 1},
    {"id": 4, "completename": "Accès > Compte et mot de passe", "is_incident": 1, "is_request": 1, "is_helpdeskvisible": 1},
    {"id": 5, "completename": "Interne > Réseau cœur", "is_incident": 1, "is_request": 0, "is_helpdeskvisible": 0},
]


def _auth(session_token):
    return session_token == "s1"


@glpi.get("/apirest.php/initSession")
async def init(app_token: str = Header(None), authorization: str = Header(None)):
    if app_token != "app" or authorization != "user_token user":
        return JSONResponse(["ERROR_GLPI_LOGIN_USER_TOKEN", "parameter user_token seems invalid"], 401)
    return {"session_token": "s1"}


@glpi.get("/apirest.php/getFullSession")
async def full(session_token: str = Header(None)):
    if not _auth(session_token):
        return JSONResponse(["ERROR_SESSION_TOKEN_INVALID", "session_token seems invalid"], 401)
    return {"session": {"glpiname": "svc-assistance", "glpiactiveprofile": {"name": "Technician"}}}


@glpi.get("/apirest.php/getGlpiConfig")
async def cfg():
    return {"cfg_glpi": {"version": "10.0.18"}}


@glpi.get("/apirest.php/User")
async def users(request: Request, session_token: str = Header(None)):
    q = request.query_params.get("searchText[name]", "")
    rows = [dict(u, locations_id=LOCS.get(u["locations_id"], 0)) for u in USERS if q.lower() in u["name"]]
    return rows


@glpi.get("/apirest.php/User/{uid}")
async def user(uid: int):
    return next(u for u in USERS if u["id"] == uid)


@glpi.get("/apirest.php/ITILCategory")
async def cats():
    return CATS


@glpi.post("/apirest.php/Ticket")
async def ticket(request: Request, session_token: str = Header(None)):
    if not _auth(session_token):
        return JSONResponse(["ERROR_SESSION_TOKEN_INVALID", ""], 401)
    data = (await request.json())["input"]
    tid = 1000 + len(TICKETS) + 1
    TICKETS[tid] = data
    return JSONResponse({"id": tid, "message": ""}, 201)


@glpi.post("/apirest.php/Document")
async def document(request: Request):
    form = await request.form()
    manifest = json.loads(form["uploadManifest"])
    f = form["filename[0]"]
    did = len(DOCS) + 1
    DOCS[did] = {"manifest": manifest["input"], "name": f.filename, "size": len(await f.read())}
    return JSONResponse({"id": did, "message": "Document ajouté", "upload_result": {}}, 201)


@glpi.get("/_debug")
async def debug():
    return {"tickets": TICKETS, "docs": DOCS}


# ------------------------------------------------------------------- LLM
llm = FastAPI()


@llm.get("/v1/models")
async def models():
    return {"data": [{"id": "mock"}, {"id": "qwen3:32b"}]}


@llm.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    dossier = body["messages"][-1]["content"]
    if '{"ok": true' in dossier or "Renvoie" in dossier:
        content = '{"ok": true, "langue": "fr"}'
    elif "## Échanges déjà faits" in dossier or "DOIT être" in dossier:
        content = json.dumps({"status": "ready", "message": "Merci, c'est clair.", "questions": [], "ticket": {
            "title": "Imprimante du 2e étage : bourrage papier répété", "summary": "L'imprimante du 2e étage affiche un bourrage papier depuis ce matin. Plusieurs collègues ne peuvent plus imprimer.",
            "facts": [{"label": "Imprimante", "value": "2e étage, près de la compta"}, {"label": "Depuis", "value": "ce matin"},
                      {"label": "Personnes touchées", "value": "plusieurs collègues"}],
            "type": "incident", "urgency": 4, "category_id": 1}}, ensure_ascii=False)
    else:
        content = "<think>je réfléchis</think>```json\n" + json.dumps({
            "status": "need_info", "message": "Merci ! Deux petites questions pour aider le technicien.",
            "questions": [
                {"id": "q1", "text": "Que se passe-t-il exactement quand vous imprimez ?", "options": ["Rien ne sort", "Bourrage papier", "Message d'erreur", "Je ne sais pas"], "multiple": False},
                {"id": "q2", "text": "D'autres collègues sont-ils touchés ?", "options": ["Oui", "Non"], "multiple": False}],
            "ticket": {"title": "Problème d'imprimante au 2e étage", "summary": "L'utilisateur signale un problème d'impression.",
                       "facts": [{"label": "Imprimante", "value": "2e étage"}], "type": "incident", "urgency": 3, "category_id": 1}},
            ensure_ascii=False) + "\n```"
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
