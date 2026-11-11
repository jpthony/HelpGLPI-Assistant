# Futures fonctionnalités

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
