# CLAUDE.md

Assistant web qui transforme la description libre d’un utilisateur peu à l’aise avec l’informatique en un ticket GLPI propre. Un LLM local pose au plus une ou deux questions, rédige le ticket, l’utilisateur relit et envoie. Connexion par SSO Windows (Kerberos / SPNEGO). Le déploiement, la configuration GLPI / AD / GPO et le dépannage sont dans `INSTALL.md` : ne pas les dupliquer ici.

## Conventions

- **Tout est en français** : textes de l’interface, messages d’erreur, commentaires, docstrings, README et messages de commit (phrase courte à l’indicatif, ex. « Ajoute un réglage… »). Garder cette règle. Les chaînes utilisent l’apostrophe typographique (`’`) et les guillemets français (`« »`).
- Les fichiers Python sont en fins de ligne **CRLF**. Ne pas utiliser `sed -i` de Git Bash dessus (conversion en LF, diff sur tout le fichier) : passer par l’outil Edit.
- Pas de build, pas de framework, pas de npm : le frontend est en HTML/CSS/JS natif avec polices auto-hébergées. Éviter d’ajouter des dépendances ; côté backend il n’y a que FastAPI, uvicorn, httpx, itsdangerous, python-multipart et gssapi.
- Pas de suite de tests. `tests/mock_services.py` contient un faux GLPI (API legacy) et un faux LLM compatible OpenAI pour essayer à la main, ce ne sont pas des tests pytest.

## Lancer en local

```bash
pip install -r backend/requirements.txt      # gssapi demande libkrb5-dev ; importé seulement en mode kerberos
uvicorn tests.mock_services:glpi --port 9001 &
uvicorn tests.mock_services:llm --port 9002 &
cd backend && DATA_DIR=/tmp/ga AUTH_MODE=dev DEV_USER=jpthony ADMIN_USERS=jpthony uvicorn app.main:app --port 8080 --reload
```

Puis dans `/admin` : GLPI `http://127.0.0.1:9001`, App-Token `app`, user_token `user`, certificat vide ; modèle `http://127.0.0.1:9002/v1`, modèle `mock`. Le faux GLPI connaît l’utilisateur `jpthony` (id GLPI 7).

Docker : `docker compose up -d --build` (ajouter `--profile proxy` pour Caddy en frontal TLS). Le conteneur tourne en uid 10001, l’application est dans `/app/backend`, le frontend dans `/app/frontend` (`FRONTEND_DIR`), la configuration dans `/data` (`DATA_DIR`).

## Architecture

```
backend/app/
  main.py    application FastAPI : pages, SSO, API utilisateur, API admin, middleware CSRF, journal en mémoire
  auth.py    3 modes de connexion (kerberos | proxy_header | dev), cookie de session signé, droits admin, pages d’erreur SSO
  config.py  ConfigStore : DEFAULTS ← data/config.json ← variables d’environnement ; masquage des secrets ; prompt et règles par défaut
  agent.py   transport LLM (compatible OpenAI ou Ollama natif), extraction du JSON, construction du dossier, normalisation
  glpi.py    LegacyGlpi (apirest.php) et V2Glpi (API OAuth GLPI 11), même interface ; petit cache à durée de vie
frontend/
  index.html + assets/js/app.js     interface utilisateur (IIFE, helpers `$` / `el()`, état dans `S`)
  admin.html + assets/js/admin.js   console d’administration (réglages, tests en direct, journal)
  assets/js/theme.js                thème auto / clair / sombre, chargé dans <head> pour éviter le flash
```

### Parcours d’une demande

1. `GET /` sert `index.html` ; le JS appelle `GET /api/me`. Sur un 401 avec `detail.sso`, il redirige vers `/auth/sso` (limité par `sessionStorage` pour éviter une boucle).
2. `/auth/sso` valide le jeton `Negotiate` avec gssapi et le keytab, puis pose le cookie `ga_session` (itsdangerous, durée `session_hours`). Les jetons NTLM (`TlRMTVNT…`) sont détectés et refusés avec une page de diagnostic. En modes `dev` et `proxy_header`, pas de cookie : l’identité est relue à chaque requête.
3. `POST /api/agent/turn` — **l’agent est sans état** : le navigateur renvoie tout le dossier à chaque tour (description, questions-réponses `exchanges`, noms des pièces jointes, `rounds_done`, `force`). Le serveur ajoute le profil GLPI et les catégories autorisées, appelle le LLM une fois (une relance si la réponse n’est pas du JSON valide), et `agent.normalize()` borne et nettoie tout. Après `max_rounds`, `force_ready` impose `status` = `ready`.
4. `POST /api/tickets` (multipart : `payload` JSON + `files`) — contrôle les pièces jointes (nombre, taille, extension), détermine le demandeur (ou `fallback_requester_id`, sinon 409), construit le contenu HTML échappé (`build_content`) et crée le ticket avec le compte de service, l’utilisateur SSO en demandeur. Un échec de pièce jointe ne bloque pas le ticket : il revient dans `warning`.

### À savoir avant de modifier le code

- **CSRF** : tout appel `/api/*` autre que GET doit porter `X-Requested-With: glpi-assistant` (middleware de `main.py` ; le helper `api()` des deux fichiers JS l’ajoute). Les nouveaux endpoints sont protégés d’office ; tout nouvel appel côté JS doit passer par `api()`.
- **Erreurs** : lever `HTTPException(status, detail={"code": ..., "message": "<message en français pour l’utilisateur>"})`. Le gestionnaire renvoie `{"detail": {...}}` et le JS affiche `detail.message`. Les endpoints de test de l’admin renvoient plutôt `{"ok": false, "error": ...}` en 200.
- **Configuration** : toujours lire via `store.section(nom)` (copie profonde : la modifier ne sauvegarde rien). L’écriture passe par `store.update()` (écriture atomique, droits 600). Les variables d’environnement de `_env_bootstrap` priment à chaque chargement et apparaissent verrouillées dans l’admin (la liste `env_locked` de `admin_get_config` doit rester alignée). Nouveau réglage : l’ajouter dans `DEFAULTS`, puis dans `admin.js` / `admin.html`.
- **Secrets** : tout nouveau champ secret doit être ajouté à `SECRET_PATHS`, pour être masqué (`MASK`) en lecture et conservé quand le formulaire renvoie `MASK`. Les tests de l’admin fusionnent les valeurs non enregistrées du formulaire via `_merged_section()`, qui ignore aussi `MASK`.
- **Garde-fou admin** : `admin_put_config` remet toujours le login de l’admin courant dans `admin_users`.
- **Client GLPI** : `glpi.get_client()` renvoie une instance partagée tant que la config GLPI ne change pas (jeton de session legacy réutilisé, rouvert une fois sur 401). `api_mode=auto` choisit legacy si `user_token` est rempli, sinon v2 si `v2_client_id` l’est. Le mode v2 utilise quand même les jetons legacy pour les pièces jointes. Enregistrer la config appelle `glpi.clear_cache()` (utilisateurs et catégories en cache 600 s).
- **Contrat LLM** : `agent.TECH_CONTRACT` définit le JSON que le modèle doit renvoyer ; il est ajouté après le `system_prompt` et les `rules` modifiables dans l’admin. Changer ce format oblige à mettre à jour `normalize()`, l’affichage dans `app.js` et le faux LLM de `mock_services.py`. `extract_json` retire les blocs `<think>` et les balises Markdown. En mode OpenAI, un HTTP 400 relance la requête sans `response_format`.
- **Journal** : `event(niveau, type, utilisateur, détail)` dans `main.py` — deque en mémoire (300 entrées), affichée dans « Journal » de l’admin. L’utiliser pour tout ce qu’un admin voudrait diagnostiquer.
- **Cache des assets** : `/assets/*` est servi avec `Cache-Control: no-cache` (revalidation) pour que les mises à jour CSS/JS soient prises en compte ; pas d’empreinte dans les noms de fichiers.
- `/docs`, `/redoc` et `/openapi.json` sont désactivés volontairement.
- Le frontend contient un easter egg Konami purement visuel (faux écran bleu + tampon « Ticket légendaire ») à la fin de `app.js` ; ne pas y toucher sans demande.

## Prévu (non implémenté)

`ROADMAP.md` : « Mes demandes » — `GET /api/my-tickets`, `GET /api/tickets/{id}`, `GET /api/tickets/{id}/status`, filtrés côté serveur sur le login de la session, puis exposés à l’agent. Ne pas exposer directement les données GLPI au navigateur.
