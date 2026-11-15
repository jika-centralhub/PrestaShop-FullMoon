# État technique à la migration

## Différences de portabilité

L’application cherche maintenant `server.py` à la racine de ce dépôt. Elle lance le MCP avec l’interpréteur Python courant, afin d’utiliser l’environnement virtuel du nouveau poste. Aucun chemin personnel du Mac d’origine n’est nécessaire. La configuration réelle et les données locales restent exclues.

## Limite connue du module paniers

`server.py` masque les champs sensibles, dont `secure_key`. Le code historique de `app/app.py` demande ce champ par `ps_get_record`, tandis que `app/carts.py` attend une valeur réelle de 32 caractères hexadécimaux. Cela produit le message « Identité sécurisée du client manquante dans PrestaShop » avant l’envoi.

Cette incompatibilité n’a pas été corrigée lors de la migration. Aucune création réelle de panier n’a été validée par l’assistant. Les tests simulés du module ne couvrent pas cette incompatibilité entre composants. Le répertoire de consultation client reste indépendant des champs du module paniers.

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
