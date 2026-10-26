# Assistant de saisie de tickets GLPI / HelpGLPI Assistant

Une page web qui aide les utilisateurs peu à l'aise à signaler un problème. Ils racontent avec leurs mots, l'assistant pose une ou deux questions seulement si une information indispensable manque, puis rédige un ticket clair qu'ils relisent avant de l'envoyer. Les captures d'écran se collent avec Ctrl + V.

- **Connexion** : SSO Windows (Kerberos / SPNEGO), sans mot de passe.
- **GLPI** : le ticket est créé par un compte de service, l'utilisateur SSO est le demandeur. API legacy `apirest.php` (GLPI 10 et 11) ou API v2 (GLPI 11).
- **IA** : modèle local, API compatible OpenAI (vLLM, llama.cpp, LM Studio, Ollama `/v1`) ou API native d'Ollama.
- **Administration** : `/admin`, tout se règle et se teste depuis le navigateur.

Dans ce guide, l'outil s'appelle `help.local.lan`. Remplacez ce nom partout s'il est différent : il doit être identique dans le DNS, le SPN, le certificat et le Caddyfile.

## Contenu

```
backend/app/       API FastAPI : SSO, agent, GLPI, administration
frontend/          interface (HTML/CSS/JS sans build, polices auto-hébergées)
deploy/            Caddyfile (HTTPS) et krb5.conf
tests/             faux GLPI et faux LLM pour essayer sans rien brancher
Dockerfile, docker-compose.yml, .env.example
```

## 1. Démarrage rapide (sans Kerberos)

Pour voir l'interface et régler GLPI et le modèle avant de s'occuper du SSO :

```bash
cp .env.example .env
# dans .env : AUTH_MODE=dev et DEV_USER=votre_login, ADMIN_USERS=votre_login
mkdir -p data secrets certs && touch secrets/http.keytab
cp /chemin/vers/racine-adcs.pem certs/ca.pem     # ou un fichier vide si tout est en HTTP
sudo chown -R 10001 data
docker compose up -d --build
```

Exposez temporairement le port (`ports: ["8080:8080"]` dans `docker-compose.yml`), ouvrez `http://<serveur>:8080/admin`, renseignez GLPI et le modèle, puis cliquez sur « Tester ».

> Le mode `dev` connecte **tout visiteur** sous l'utilisateur simulé. Ne le laissez jamais en production.

## 2. GLPI

### Compte de service

1. Créez un utilisateur local, par exemple `svc-assistance`.
2. Donnez-lui un profil qui peut créer des tickets **au nom d'un autre utilisateur** (un profil de type Technicien suffit ; évitez Super-Admin), sur les entités concernées, en récursif.
3. Dans sa fiche, onglet « Paramètres », générez le **jeton d'API** (user_token).

### API legacy (recommandée, GLPI 10 et 11)

1. Configuration > Générale > API : activez l'API REST. Sur GLPI 11, activez aussi l'API legacy.
2. Ajoutez un client API, limité à l'adresse IP du serveur Docker, et récupérez son **App-Token**.
3. Dans `/admin` > Connexion à GLPI : adresse, App-Token, user_token, puis « Tester la connexion ».

Le test affiche la version, le profil actif du compte de service, le nombre de catégories et vérifie qu'un login donné est bien trouvé.

### API v2 (GLPI 11, facultatif)

Configuration > Clients OAuth : client avec l'autorisation « Password » et le scope « api ». Renseignez client ID, secret, identifiant et mot de passe du compte de service, et choisissez « API haut niveau v2 ».

Limites connues : ce mode a été écrit d'après la documentation officielle mais n'a pas encore été validé sur votre instance. Le demandeur est ajouté après la création, et le compte de service est retiré des demandeurs au mieux. Les pièces jointes passent par l'API legacy : laissez donc aussi ses jetons. Validez les chemins sur `https://support.local.lan/api.php/doc`.

### Réglages utiles

- **Entité** : vide = entité par défaut du demandeur.
- **Source de la demande** : l'ID de la source « Helpdesk » (ou une source dédiée « Assistant ») pour repérer ces tickets.
- **Demandeur de repli** : si un utilisateur SSO n'existe pas dans GLPI ; sinon la création est refusée avec un message clair.
- **Catégories** : seules celles visibles dans l'interface simplifiée sont proposées. Cochez-en moins dans « Comportement de l'agent » pour des suggestions plus justes.
- La taille maximale des pièces jointes doit rester sous la limite d'upload de GLPI et de PHP.

## 3. Modèle IA

Dans `/admin` > Modèle IA :

- Compatible OpenAI : `https://llm.local.lan/v1` (terminer par `/v1`).
- Ollama natif : `http://<hôte>:11434`.

« Tester le modèle » liste les modèles, mesure le temps de réponse et vérifie que la réponse est bien du JSON. Si le serveur refuse `response_format`, désactivez « Forcer une réponse JSON ».

Conseils : un modèle instruct de 14 à 32 milliards de paramètres suffit, température 0,1 à 0,3. Les blocs `<think>` des modèles de raisonnement sont retirés automatiquement, mais ils rallongent l'attente : préférez un mode sans réflexion si le modèle en propose un. Utilisez « Essayer l'agent » pour ajuster le prompt et les règles.

Si le modèle est indisponible, l'utilisateur peut quand même remplir et envoyer la fiche lui-même.

## 4. SSO Kerberos

### DNS

Créez un enregistrement **A** (pas un CNAME) `help.local.lan` vers le serveur. Les navigateurs construisent le SPN à partir du nom canonique : un CNAME ou une adresse IP fait échouer Kerberos (repli NTLM).

### Compte de service AD et keytab

Sur un contrôleur de domaine (PowerShell administrateur) :

```powershell
New-ADUser -Name "svc-assistance-http" -SamAccountName svc-assistance-http `
  -AccountPassword (Read-Host -AsSecureString "Mot de passe") -Enabled $true `
  -PasswordNeverExpires $true -KerberosEncryptionType AES256

ktpass -princ HTTP/help.local.lan@LOCAL.LAN -mapuser LOCAL\svc-assistance-http `
  -pass * -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -out http.keytab

setspn -Q HTTP/help.local.lan    # doit renvoyer UN seul compte
```

Copiez `http.keytab` dans `secrets/`, puis :

```bash
sudo chown 10001 secrets/http.keytab && sudo chmod 400 secrets/http.keytab
```

Adaptez `deploy/krb5.conf` si le royaume n'est pas `LOCAL.LAN`. L'application ne fait que **valider** les tickets : elle ne contacte pas le KDC et ne stocke aucun mot de passe.

Vérification dans le conteneur :

```bash
docker exec glpi-assistant klist -k /etc/krb5/http.keytab
```

### Navigateurs (GPO)

- **Edge et Chrome** : stratégie `AuthServerAllowlist` = `help.local.lan` (ou `*.local.lan`).
- **Firefox** : `network.negotiate-auth.trusted-uris` = `help.local.lan`.

Puis, dans `.env`, `AUTH_MODE=kerberos` et `docker compose up -d`.

### Dépannage

| Symptôme | Cause probable |
|---|---|
| Page « Connexion automatique impossible », aucun jeton reçu | Site absent de `AuthServerAllowlist`, poste hors domaine, ou accès par IP |
| Message « méthode non prise en charge » (jeton NTLM) | SPN absent ou en double, CNAME, ou accès par IP |
| « Connexion refusée » avec erreur de déchiffrement | Keytab périmé (mot de passe du compte changé), mauvais kvno, chiffrement AES non activé |
| Erreur d'horloge | Le serveur Docker doit être synchronisé (NTP) avec les contrôleurs de domaine |

Les détails techniques sont affichés dans la page d'erreur (« Détails pour le support ») et dans `/admin` > Journal.

### Alternative : SSO par le reverse proxy

Si un Apache avec `mod_auth_gssapi` existe déjà, choisissez « En-tête transmis par un reverse proxy » et faites transmettre le login dans `X-Remote-User`. Dans ce cas, le port de l'application ne doit être joignable que par le proxy.

## 5. HTTPS avec Caddy

Émettez un certificat pour `help.local.lan` depuis votre PKI AD CS et placez-le dans `certs/` :

- `certs/help.local.lan.crt` (avec la chaîne intermédiaire)
- `certs/help.local.lan.key`

Puis :

```bash
docker compose --profile proxy up -d
```

Si la box qui héberge déjà Caddy sert l'application, ajoutez plutôt le bloc de `deploy/Caddyfile` à sa configuration. L'en-tête `Authorization: Negotiate` est transmis tel quel.

`certs/ca.pem` (racine AD CS) est utilisé pour vérifier les certificats de GLPI et du serveur LLM.

## 6. Administration

`/admin` est réservé aux logins listés dans « Accès > Administrateurs » (et dans `ADMIN_USERS`). Votre propre login ne peut pas être retiré.

| Section | Ce qu'on y règle |
|---|---|
| Connexion à GLPI | API, jetons, certificats, entité, source, demandeur de repli, préfixe des titres, lien vers les tickets |
| Modèle IA | type d'API, adresse, clé, modèle, température, délai, mode JSON |
| Comportement de l'agent | séries et nombre de questions, personnalité, informations indispensables par type de problème, catégories proposées, essai en direct |
| Page d'accueil | nom, couleur, logo, textes, raccourcis |
| Pièces jointes | nombre, taille, extensions |
| Accès | méthode de connexion, principal de service, normalisation du login, durée de session, administrateurs |
| Journal | connexions, tours de l'agent, tickets créés, erreurs (300 derniers, en mémoire) |

Les secrets ne sont jamais renvoyés au navigateur. La configuration est stockée dans `data/config.json` (droits 600). Une variable définie dans `.env` prime sur l'administration et y apparaît verrouillée.

## 7. Ce que reçoit le technicien

- un titre court et factuel ;
- un résumé à la troisième personne ;
- une liste de faits (appareil, logiciel, depuis quand, personnes touchées…) ;
- le message d'origine et les réponses aux questions (désactivable) ;
- le type, l'urgence (de 2 à 5 selon la gêne déclarée), la catégorie, le lieu du demandeur et les pièces jointes.

Si une pièce jointe est refusée par GLPI, le ticket est quand même créé : l'utilisateur en est averti et l'erreur apparaît dans le journal.

## 8. Futures fonctionnalités

Les fonctionnalités suivantes sont prévues pour compléter le parcours de suivi et de gestion des demandes :

- **Suivi de l'état d'un ticket** : permettre à l'utilisateur connecté de consulter le statut actuel d'un ticket GLPI (nouveau, en cours, en attente, résolu, fermé, etc.).
- **Liste des tickets de l'utilisateur connecté** : afficher les demandes créées par le même login que celui de la session active.
- **Historique des demandes** : garder une vue simple des tickets déjà soumis, avec titre, date, statut et lien vers le ticket GLPI.
- **Détail d'un ticket** : afficher les informations principales d'un ticket (titre, description, priorité, catégorie, date de création, dernier changement de statut).
- **Notifications** : signaler les changements de statut importants (par exemple : "Votre ticket a été mis à jour" ou "Le technicien a répondu").

Ces fonctions peuvent être exposées par une API dédiée côté backend, puis intégrées à l'interface web sous une section "Mes demandes" ou "Suivi de mes tickets".

La bonne manière de les implémenter reste de passer par une API backend dédiée, puis de laisser l'agent interroger cette API au nom de l'utilisateur connecté. Exemple de logique cible :

- `GET /api/my-tickets` : retourne les tickets du demandeur connecté (ID, titre, statut, date, priorité, lien GLPI).
- `GET /api/tickets/{id}` : retourne le détail d'un ticket précis.
- `GET /api/tickets/{id}/status` : retourne uniquement le statut courant et les derniers changements.
- L'agent reçoit ensuite une instruction du type : "Liste mes tickets et donne-moi leur statut actuel" ; il appelle l'API, traite les résultats et répond en français.

Cette approche évite d'exposer directement les données GLPI au navigateur et permet de garder le contrôle sur les droits, les filtres et la sécurité.

Pour être clair sur le fonctionnement attendu : une fois cette fonctionnalité ajoutée, l'utilisateur pourra demander à l'agent des phrases du type :

- "Liste-moi mes tickets ouverts" ;
- "Quels sont mes tickets en cours ?" ;
- "Quel est le statut de mon ticket n°1234 ?" ;
- "Donne-moi le résumé de mes demandes en attente".

L'agent utilisera alors l'API backend dédiée, qui récupère les tickets du login de la session courante dans GLPI, puis renvoie uniquement les informations autorisées. L’utilisateur ne voit donc pas les tickets des autres personnes, et l'agent ne répond qu'avec les données qui lui sont accessibles.

## 9. Essayer sans GLPI ni modèle

```bash
pip install -r backend/requirements.txt
uvicorn tests.mock_services:glpi --port 9001 &
uvicorn tests.mock_services:llm --port 9002 &
cd backend && DATA_DIR=/tmp/ga AUTH_MODE=dev DEV_USER=jpthony ADMIN_USERS=jpthony uvicorn app.main:app --port 8080
```

Dans `/admin` :

- GLPI : `http://127.0.0.1:9001`, App-Token `app`, user_token `user`, certificat vide.
- Modèle : `http://127.0.0.1:9002/v1`, modèle `mock`.

## Sécurité

- **Session** : cookie signé, HttpOnly, SameSite=Lax, Secure derrière HTTPS ; durée réglable.
- **Requêtes** : les appels qui modifient quelque chose exigent un en-tête propre à l'application (protection CSRF).
- **Redirections** : seules les redirections internes sont acceptées après connexion.
- **Contenu** : le texte saisi est échappé avant d'être envoyé à GLPI.
- **Fichiers** : les pièces jointes sont filtrées par extension et par taille.
- **Questions de l'agent** : l'agent a pour consigne de ne jamais demander de mot de passe.
- **Conteneur** : il tourne avec un utilisateur non root.
