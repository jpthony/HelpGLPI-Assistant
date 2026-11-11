# Assistant de saisie de tickets GLPI / HelpGLPI Assistant

Une page web qui aide les utilisateurs peu à l'aise à signaler un problème. Ils racontent avec leurs mots, l'assistant pose une ou deux questions seulement si une information indispensable manque, puis rédige un ticket clair qu'ils relisent avant de l'envoyer. Les captures d'écran se collent avec Ctrl + V.

- **Connexion** : SSO Windows (Kerberos / SPNEGO), sans mot de passe.
- **GLPI** : le ticket est créé par un compte de service, l'utilisateur SSO est le demandeur. API legacy `apirest.php` (GLPI 10 et 11) ou API v2 (GLPI 11).
- **IA** : modèle local, API compatible OpenAI (vLLM, llama.cpp, LM Studio, Ollama `/v1`) ou API native d'Ollama.
- **Administration** : `/admin`, tout se règle et se teste depuis le navigateur.

![Capture d'écran de l'assistant](docs/screenshot.jpg)

## Contenu

```
backend/app/       API FastAPI : SSO, agent, GLPI, administration
frontend/          interface (HTML/CSS/JS sans build, polices auto-hébergées)
deploy/            Caddyfile (HTTPS) et krb5.conf
tests/             faux GLPI et faux LLM pour essayer sans rien brancher
Dockerfile, docker-compose.yml, .env.example
```

## Installation

Démarrage rapide, GLPI, modèle IA, SSO Kerberos, HTTPS et administration : voir [INSTALL.md](INSTALL.md).

## Ce que reçoit le technicien

- un titre court et factuel ;
- un résumé à la troisième personne ;
- une liste de faits (appareil, logiciel, depuis quand, personnes touchées…) ;
- le message d'origine et les réponses aux questions (désactivable) ;
- le type, l'urgence (de 2 à 5 selon la gêne déclarée), la catégorie, le lieu du demandeur et les pièces jointes.

Si une pièce jointe est refusée par GLPI, le ticket est quand même créé : l'utilisateur en est averti et l'erreur apparaît dans le journal.

## Futures fonctionnalités

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

## Sécurité

- **Session** : cookie signé, HttpOnly, SameSite=Lax, Secure derrière HTTPS ; durée réglable.
- **Requêtes** : les appels qui modifient quelque chose exigent un en-tête propre à l'application (protection CSRF).
- **Redirections** : seules les redirections internes sont acceptées après connexion.
- **Contenu** : le texte saisi est échappé avant d'être envoyé à GLPI.
- **Fichiers** : les pièces jointes sont filtrées par extension et par taille.
- **Questions de l'agent** : l'agent a pour consigne de ne jamais demander de mot de passe.
- **Conteneur** : il tourne avec un utilisateur non root.

## Soutien

L'outil vous rend service ?

[<img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="41" width="174">](https://buymeacoffee.com/jpthony)
