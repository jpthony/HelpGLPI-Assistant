# Installation

Dans ce guide, l'outil s'appelle `help.local.lan`. Remplacez ce nom partout s'il est différent : il doit être identique dans le DNS, le SPN, le certificat et le Caddyfile.

## 1. Démarrage rapide (sans Kerberos)

Pour voir l'interface et régler GLPI et le modèle avant de s'occuper du SSO :

```bash
cp .env.example .env
# dans .env : AUTH_MODE=dev et DEV_USER=votre_login, ADMIN_USERS=votre_login
mkdir -p data secrets certs && touch secrets/help.keytab
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
  -pass * -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -out help.keytab

setspn -Q HTTP/help.local.lan    # doit renvoyer UN seul compte
```

Copiez `help.keytab` dans `secrets/`, puis :

```bash
sudo chown 10001 secrets/help.keytab && sudo chmod 400 secrets/help.keytab
```

Adaptez `deploy/krb5.conf` si le royaume n'est pas `LOCAL.LAN`. L'application ne fait que **valider** les tickets : elle ne contacte pas le KDC et ne stocke aucun mot de passe.

Vérification dans le conteneur :

```bash
docker exec glpi-assistant klist -k /etc/krb5/http.keytab
```

### Navigateurs (GPO)

Sans ces stratégies, le navigateur n'envoie pas l'identité Windows de lui-même : il affiche une fenêtre de connexion (qui ne fonctionne qu'avec `login@local.lan`). Déclarez le nom complet **et** le nom court si les utilisateurs tapent `help/`.

- **Edge et Chrome** (couvre les deux) : *Configuration ordinateur > Modèles d'administration > Composants Windows > Internet Explorer > Panneau de configuration Internet > Page Sécurité > Liste des attributions de sites aux zones* : `help` = `1` et `help.local.lan` = `1` (zone Intranet local, connexion automatique par défaut).
- **Ou** la stratégie dédiée de chaque navigateur (modèles ADMX Edge et Chrome) : `AuthServerAllowlist` = `help,help.local.lan` (ou `help,*.local.lan`).
- **Firefox** : `network.negotiate-auth.trusted-uris` = `help,.local.lan` (ou `policies.json` : `"Authentication": { "SPNEGO": ["help", ".local.lan"] }`).

Vérification sur un poste : `gpupdate /force`, fermer complètement le navigateur, contrôler `edge://policy`, puis ouvrir `help/` : aucune fenêtre ne doit apparaître.

Puis, dans `.env`, `AUTH_MODE=kerberos` et `docker compose up -d`.

### Dépannage

| Symptôme | Cause probable |
|---|---|
| Le navigateur demande identifiant et mot de passe (ça passe avec `login@local.lan`) | Site non déclaré en zone Intranet ni dans `AuthServerAllowlist` (voir GPO ci-dessus) |
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

## 7. Essayer sans GLPI ni modèle

```bash
pip install -r backend/requirements.txt
uvicorn tests.mock_services:glpi --port 9001 &
uvicorn tests.mock_services:llm --port 9002 &
cd backend && DATA_DIR=/tmp/ga AUTH_MODE=dev DEV_USER=jpthony ADMIN_USERS=jpthony uvicorn app.main:app --port 8080
```

Dans `/admin` :

- GLPI : `http://127.0.0.1:9001`, App-Token `app`, user_token `user`, certificat vide.
- Modèle : `http://127.0.0.1:9002/v1`, modèle `mock`.
