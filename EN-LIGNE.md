# Version en ligne (branche `en-ligne`)

Cette branche permet d'utiliser l'outil depuis un simple navigateur, à plusieurs, sans rien installer sur les postes. **La branche `main` et les installations Mac existantes ne changent pas** : l'installateur Mac télécharge toujours `main`.

## Ce qui change par rapport à la version installée sur un poste

| | Version poste (`main`) | Version en ligne (`en-ligne`) |
|---|---|---|
| Accès | une session ouverte par le lanceur, sur ce poste seulement | une adresse https, chaque personne se connecte avec son identifiant |
| Clés PrestaShop | fichier `.env` et clé des paniers saisie dans la page | réglées une fois sur le serveur par l'administrateur, jamais dans la page |
| Registre anti-doublon | un par poste | un seul, partagé, avec le nom de la personne qui a créé chaque panier |
| Installation | Python sur chaque poste | rien sur les postes |

Le lanceur local (`Lancer FullMoon.command` / `.bat`) fonctionne toujours sur cette branche, à l'identique.

## Sécurité

- **Connexion par personne** : mots de passe chiffrés (scrypt), jamais stockés en clair ; code à 6 chiffres sur téléphone en option, par compte.
- **Sessions** : cookie protégé (`Secure`, `HttpOnly`, `SameSite=Strict`), fin après 4 h d'inactivité et au plus 12 h après la connexion. Déconnexion depuis l'en-tête.
- **Essais limités** : 5 erreurs en 15 minutes bloquent le compte et l'adresse pendant 15 minutes.
- **Liste d'adresses autorisées** (recommandé) : si l'équipe travaille depuis un bureau, seules ses adresses IP voient l'outil.
- **Contrôles de chaque requête** : nom de domaine attendu, origine de la page, session valide. HTTPS imposé.
- **Clés PrestaShop à droits minimaux**, inchangées : une clé en lecture seule, une clé limitée à la lecture et à la création de paniers. L'outil refuse une clé qui aurait plus de droits.
- **Aucune donnée client stockée** : l'outil lit la boutique au moment où on le lui demande. Le serveur ne garde que les comptes, les sessions et le registre des paniers (référence de commande, n° client, n° panier, auteur).
- **Journal** : connexions réussies ou refusées et paniers créés, avec l'auteur ; jamais de mot de passe ni de donnée client.

## Réglages du serveur (variables d'environnement)

| Variable | Obligatoire | Contenu |
|---|---|---|
| `FULLMOON_PUBLIC_URL` | oui | adresse https de l'outil, par exemple `https://fullmoon-outil.osc-fr1.scalingo.io` |
| `PS_SHOP_URL` | oui | `https://fullmoon-distribution.fr/` |
| `PS_API_KEY` | oui | **nouvelle** clé Webservice dédiée au serveur, GET uniquement |
| `PS_CART_KEY` | pour créer des paniers | **nouvelle** clé dédiée au serveur, GET et POST sur `carts` uniquement |
| `FULLMOON_ALLOWED_IPS` | recommandé | adresses autorisées, séparées par des virgules (ex. `81.250.1.2, 90.12.0.0/16`) |
| `DATABASE_URL` / `SCALINGO_POSTGRESQL_URL` | sur Scalingo | posée automatiquement par le module PostgreSQL ; sans elle, registre SQLite dans `FULLMOON_DATA_DIR` |
| `FULLMOON_BIND`, `PORT` | non | adresse et port d'écoute (Scalingo fixe `PORT` seul) |
| `FULLMOON_TRUST_PROXY` | non | `1` par défaut : l'outil est derrière un relais https (Scalingo, Apache) |

Ne réutilisez pas les clés des postes : en cas de problème, chaque clé se révoque séparément dans PrestaShop.

## Mise en ligne sur Scalingo

À faire par le titulaire du compte (création du compte, paiement et clés) :

1. Créer un compte sur scalingo.com et une application, région **Paris (osc-fr1)**, taille de conteneur **M**.
2. Ajouter le module **PostgreSQL** (plus petite offre).
3. Dans PrestaShop → Paramètres avancés → Webservice, créer les deux nouvelles clés décrites plus haut.
4. Dans l'application Scalingo → Environment, saisir les variables du tableau.
5. Dans Code → GitHub, relier le dépôt et choisir la branche **`en-ligne`** pour le déploiement automatique, puis lancer un premier déploiement.

Ensuite, créer les comptes avec la ligne de commande Scalingo (le mot de passe se tape de façon masquée) :

```sh
scalingo --app NOM-DE-L-APPLICATION run python app/app.py --add-user marie --totp
```

`--totp` active le code à 6 chiffres et affiche la clé à saisir dans l'application d'authentification du téléphone. Autres commandes : `--list-users`, `--disable-user marie` (ferme aussi ses sessions). Relancer `--add-user` sur un compte existant change son mot de passe.

## Variante : serveur de la boutique (OVH)

Même programme, sans PostgreSQL (registre SQLite dans `FULLMOON_DATA_DIR`), derrière Apache :

- service qui lance `python -B app/app.py --online` avec `FULLMOON_BIND=127.0.0.1` et `PORT=8790` ;
- hôte virtuel Apache `outil.<domaine>` avec certificat Let's Encrypt, `ProxyPass / http://127.0.0.1:8790/` et `RequestHeader set X-Forwarded-Proto https` ;
- dossier `FULLMOON_DATA_DIR` hors du site web, lisible par le seul compte du service, compris dans les sauvegardes.

## Essai sur un poste

`FULLMOON_PUBLIC_URL=http://localhost:8795` est accepté pour un essai local (et seulement `localhost`). Tests hors ligne : `python -B -m unittest discover -s tests`.

## Limites connues

- Le stockage PostgreSQL n'a pas pu être testé sur le poste de développement : à vérifier au premier déploiement (création de compte, connexion, historique des paniers).
- La version de Python est fixée par `.python-version` (3.13) : à confirmer dans le journal du premier déploiement Scalingo.
- Comme sur `main`, aucune création réelle de panier n'a encore été validée sur la boutique.
