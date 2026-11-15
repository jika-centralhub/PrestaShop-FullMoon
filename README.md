# PrestaShop FullMoon

Sources de l’installation locale FullMoon Distribution, transférées pour reprise sur un autre poste. Le serveur MCP expose des outils en lecture seule. L’interface locale importe Excel/CSV, consulte les stocks et les clients et affiche leurs coordonnées.

## Installation sur un autre poste

Prérequis : Git et Python 3.13. Les commandes suivantes concernent macOS/Linux.

```sh
git clone https://github.com/jika-centralhub/PrestaShop-FullMoon.git
cd PrestaShop-FullMoon
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
cp .env.example .env
chmod 600 .env
```

Renseigner `PS_API_KEY` dans `.env`, localement, avec la clé PrestaShop en lecture seule. Ne jamais la coller dans GitHub, un ticket ou une conversation. L’URL autorisée est `https://fullmoon-distribution.fr/` ; le client bloque les autres destinations, les redirections et les méthodes autres que GET.

```sh
.venv/bin/python -B app/app.py
```

Sur Mac, le lanceur `Lancer FullMoon.command` est également disponible. Sur Windows, utiliser `py -3.13 -m venv .venv`, puis `.venv\Scripts\python.exe` à la place de `.venv/bin/python` ; créer le fichier `.env` manuellement depuis l’exemple. Windows n’a pas été testé.

L’outil écoute seulement sur `127.0.0.1:8790` et ouvre une URL avec une session locale. Garder le processus ouvert. Le répertoire clients utilise le MCP en lecture seule et ne nécessite pas la clé dédiée aux paniers. L’adresse e-mail et les adresses sont affichées à la sélection, sans attribuer automatiquement les rôles livraison/facturation.

La facturation HTML locale et le calcul de TVA ont été retirés de l’interface et du serveur. Tous les montants affichés sont HT et les prix importés doivent être HT. Les réglages fiscaux de PrestaShop ne sont pas modifiés. Les éventuelles archives locales sont conservées.

## État de la reprise

- Lecture API et découverte MCP validées sur le Mac d’origine.
- Import CSV/XLS/XLSX et contrôles de stock testés. Les doublons partagent les mêmes disponibilités.
- Répertoire clients paginé, filtre et consultation des coordonnées testés sur le Mac d’origine.
- Les prix catalogue et totaux locaux sont indicatifs, hors règles particulières, remises client et transport.
- Module paniers : l’erreur « Identité sécurisée du client manquante » est corrigée (voir `REPRISE.md`). Tests hors ligne au vert ; une création réelle sur la boutique reste **à valider**.
- Aucune clé, donnée client, facture, commande importée ou base locale n’est incluse.

## Organisation

- `server.py`, `ps_client.py` — serveur MCP et client PrestaShop durci.
- `app/` — serveur HTTP local, interface et sources historiques du module paniers.
- `tests/` — tests hors ligne avec données synthétiques.
- `start_tunnel.py`, `tunnel-profiles/fullmoon.example.yaml` — lanceur et modèle du tunnel privé.
- `REPRISE.md` — état technique, limites et données à conserver séparément.

## Tests hors ligne

```sh
.venv/bin/python -B -m unittest discover -s tests -v
```

Ces tests ne créent aucun panier et n’écrivent pas dans PrestaShop. Les tests du module historique utilisent des réponses simulées et ne prouvent pas son fonctionnement réel.

## Tunnel ChatGPT facultatif

L’outil HTML utilise directement le MCP en stdio et n’a pas besoin du tunnel. Pour reprendre la connexion ChatGPT, consulter la documentation officielle de [Secure MCP Tunnel](https://github.com/openai/tunnel-client). Le binaire natif, les identifiants du tunnel et la clé OpenAI sont absents du dépôt.

Le poste d’origine utilisait `openai/tunnel-client` v0.0.21 pour macOS ARM64, vérifié avant installation. Vérifier la disponibilité, la compatibilité et la somme de contrôle de la version officielle sur le nouveau poste. Le lanceur attend le binaire dans `bin/tunnel-client` et un profil local `tunnel-profiles/fullmoon.yaml`. Copier le modèle et renseigner localement l’identifiant du tunnel et les chemins absolus du nouveau poste. La clé runtime est saisie de façon masquée et reste dans l’environnement du processus.

## Origine et licence

Basé sur [Breczek/prestashop-mcp-legacy](https://github.com/Breczek/prestashop-mcp-legacy), licence MIT conservée dans `LICENSE`. Les dépendances tierces sont installées depuis leurs distributions Python et ne sont pas vendues avec ce dépôt.
