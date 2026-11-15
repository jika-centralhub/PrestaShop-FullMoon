# État technique à la migration

## Différences de portabilité

L’application cherche maintenant `server.py` à la racine de ce dépôt. Elle lance le MCP avec l’interpréteur Python courant, afin d’utiliser l’environnement virtuel du nouveau poste. Aucun chemin personnel du Mac d’origine n’est nécessaire. La configuration réelle et les données locales restent exclues.

## Module paniers : clé sécurisée du client (corrigé)

`server.py` masque les champs sensibles, dont `secure_key`, pour qu’ils n’atteignent jamais ChatGPT. Le code historique de `app/app.py` demandait pourtant ce champ par `ps_get_record`, via le MCP. Il recevait donc une valeur vide, et `app/carts.py`, qui attend 32 caractères hexadécimaux, bloquait l’envoi avec le message « Identité sécurisée du client manquante dans PrestaShop ».

Correction : `customer_secure_key()` dans `app/app.py` lit désormais ce champ directement, hors MCP, avec la clé en lecture seule du fichier `.env` (GET `customers/{id}` par le client durci de `ps_client.py`). La valeur reste en mémoire dans le processus local et ne part que dans le POST du panier : elle n’est ni renvoyée à la page, ni journalisée, ni écrite dans le registre. Le masquage du MCP est inchangé. La lecture vérifie aussi que l’identifiant et la boutique du client correspondent.

`tests/test_secure_key.py` couvre l’enchaînement entre composants : client masqué par le vrai `server.py`, lecture locale de la clé, création simulée d’un panier, absence de la clé dans la préparation et le registre. Une création réelle de panier sur la boutique reste à valider. Le répertoire de consultation client reste indépendant des champs du module paniers.

La sélection des coordonnées n’envoie aucune écriture. Les adresses enregistrées ne permettent pas, à elles seules, de déterminer une adresse de facturation ou de livraison par défaut.

## Fichiers volontairement non publiés

- `.env`, clés PrestaShop et OpenAI, profils réels du tunnel.
- URL de session locale et identifiants de session.
- Répertoires `factures/`, `paniers/` et leurs registres SQLite.
- CSV/Excel de clients, résultats de contrôles réels et exports commerciaux.
- Environnement Python, bibliothèques vendoriées et binaire du tunnel.

Les registres et documents restent sur le poste d’origine. Pour une continuité d’archivage, les conserver et les transférer séparément par un canal privé. Le registre des factures protège la numérotation locale et le registre des paniers protège contre les doubles envois. Ne pas les remplacer par des registres vides lors d’une reprise de données existantes.

Les secrets doivent être configurés localement sur le nouveau poste. Le dépôt ne permet pas de restaurer les clés ou une session authentifiée.

## Validation de migration

Les tests hors ligne vérifient les imports, les calculs et certaines protections simulées. Les lectures réelles ont été vérifiées sur le poste d’origine. L’installation complète et les connexions authentifiées sur le nouveau poste restent à vérifier.
