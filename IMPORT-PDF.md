# Import PDF et identification du client

Cette branche ajoute l'import local de PDF texte au format cinq colonnes : référence interne, référence fournisseur, EAN, quantité et nom du produit. Les références et quantités réparties sur plusieurs lignes sont reconstituées dans leur colonne. Une prévisualisation impose une vérification manuelle. Les scans et les formats non reconnus sont refusés ; aucun OCR n'est inclus.

La recherche du client utilise uniquement un e-mail explicitement indiqué dans l'en-tête avec une mention « Acheteur ». Une adresse absente ou ambiguë exige une sélection manuelle. Le point d'entrée `/customers/match` consulte le MCP en lecture seule et retient uniquement les clients actifs, non supprimés, de la boutique choisie avec un e-mail exactement correspondant. Un résultat unique sélectionne le répertoire de consultation et affiche les coordonnées ; il ne renseigne pas les champs de création du panier. Aucun `secure_key` n'est demandé ou retourné.

## Installation et vérification

Installer les dépendances mises à jour avec `python -m pip install -r requirements-lock.txt`, puis lancer `python -m unittest discover -s tests -q` depuis la racine du dépôt. Redémarrer l'application après mise à jour.

## Reprise du travail

Base : commit f83c0f239653f61eca8c012c7a93d502aba93845 de main. Les changements locaux PDF/client sont conservés ensemble sur cette branche. Fusionné dans main avec la correction des paniers (clé sécurisée lue hors MCP). La lecture du PDF renvoie désormais son résultat en UTF-8, ce qui la fait fonctionner aussi sous Windows.

Les tests automatisés couvrent notamment l'absence de client, les doublons, les filtres de boutique/statut, la correspondance exacte et l'exclusion des secrets. Le PDF utilisateur a été vérifié localement ; son e-mail acheteur ne correspondait à aucun client actif de la boutique testée. Aucun document utilisateur, identifiant secret ou donnée client réelle n'est livré dans cette branche.
