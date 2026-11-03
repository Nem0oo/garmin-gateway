# Plan : regrouper garmin-mcp et garmin-bridge en un service unique

Date : 2026-11-03. Statut : validé.

## Objectif

Un seul service Garmin qui expose :
- **MCP** (`/mcp`) : lecture via l'API officielle (outils actuels de garmin-mcp) + écriture des séances via l'API non officielle (outils actuels de garmin-bridge).
- **API REST en lecture seule** (`/api/v1/...`) : données d'activité pour les clients non-MCP, d'abord training (moteur de scoring).

Deux clés distinctes : une pour le MCP, une pour l'API REST. Pas de niveaux de droits au sein d'une même porte : l'API est en lecture seule par construction (uniquement des `GET`), le MCP a tout.

## Décisions de base

| Sujet | Décision | Raison |
|---|---|---|
| Dépôt | Faire évoluer `garmin-mcp`, renommé `garmin-gateway` sur GitHub à l'étape 4 | Il a déjà tests, CI avec `needs: test`, auth Bearer stricte, conteneur durci. GitHub redirige l'ancien nom. |
| Paquet Python | `src/garmin_gateway/` (renommage de `src/garmin_mcp/` à l'étape 1) | Le nom doit refléter le contenu. |
| Image | `nem0oo/garmin-gateway` | Ne pas écraser `garmin-mcp:latest` pendant la transition (les deux tournent en parallèle). |
| Lecture | API officielle uniquement | Pas de token MFA, pas de risque de blocage. |
| Écriture | API non officielle (`garminconnect[workout]`) | Seule à savoir créer/planifier des séances. |
| Backends | Les deux toujours présents, une seule instance (un seul compte Garmin) | Le fichier de tokens peut manquer au démarrage (le premier login se fait dans le conteneur lancé) : les outils d'écriture renvoient alors une erreur claire, la lecture fonctionne. |
| Format de séries REST | Reprend le contrat attendu par training (`GarminActivity`), enrichi | training ne change que l'URL et la clé. |
| Source des séries | Fichier FIT (`/download-service/files/activity/{id}`, zip) décodé côté gateway | Seul endpoint officiel qui donne la seconde par seconde (puissance, température). |

## Architecture cible

```
src/garmin_gateway/
  __main__.py
  config.py               # env vars, validation des clés, chemin des tokens
  security.py             # middleware ASGI : une clé par préfixe de chemin
  app.py                  # assemble Starlette : /health, /mcp, /api/v1
  garmin/
    official.py           # = garmin_client.py actuel (clé perso -> token d'accès, refresh sur 401)
    unofficial.py         # client garminconnect : chargement des tokens, appels en thread
    fit.py                # zip -> FIT -> séries (timestamps, power_w, temperature_c, ...)
  mcp/
    server.py             # instance FastMCP, server_health, enregistrement conditionnel
    read_tools.py         # outils actuels de garmin-mcp
    workout_tools.py      # outils de garmin-bridge + modèles pydantic des étapes
  rest/
    routes.py             # routes GET /api/v1/...
  login.py                # login interactif garminconnect (repris de garmin-bridge)
tests/
  test_config.py
  test_security.py
  test_official_client.py # = test_garmin_client.py actuel
  test_read_tools.py      # = test_server.py actuel
  test_workout_tools.py   # nouveau : construction du payload (_build_steps, _target_fields)
  test_fit.py             # nouveau, avec un petit FIT de test dans tests/fixtures/
  test_rest.py            # nouveau
```

## Configuration

| Variable | Requise | Rôle |
|---|---|---|
| `MCP_API_KEY` | oui | Clé du MCP, ≥ 32 caractères |
| `REST_API_KEY` | oui | Clé de l'API REST, ≥ 32 caractères, **différente** de `MCP_API_KEY` (refus au démarrage sinon) |
| `DOMAIN` | oui | Hôte public (validation `Host`/`Origin` du MCP) |
| `GARMIN_PERSONAL_API_KEY` | oui | Clé Personal API (lecture : outils MCP de lecture + API REST) |
| `GARMIN_TOKEN_STORE` | non | Fichier de tokens garminconnect (écriture). Défaut : `/data/garmin_tokens.json`. Peut être absent au démarrage. |
| `PORT`, `HTTP_TIMEOUT`, `GARMIN_BASE_URL` | non | Inchangés |
| `MAX_FILE_BYTES` | non | Limite du fichier renvoyé par l'outil MCP `get_activity_file` (inchangé, 5 Mo) |
| `MAX_FIT_BYTES` | non | Limite du zip téléchargé pour l'API REST (défaut 20 Mo) |

## Authentification

- `security.py` associe un préfixe de chemin à une clé :
  - `/mcp` → `MCP_API_KEY`
  - `/api/` → `REST_API_KEY`
  - `/health` → ouvert
  - tout autre chemin → 404 (pas de 401 qui confirmerait l'existence d'une route).
- Règles reprises de garmin-mcp : `Authorization: Bearer` uniquement, préfixe insensible à la casse, comparaison `hmac.compare_digest` sur bytes, 401 JSON avec `WWW-Authenticate: Bearer`, middleware ASGI pur, websocket fermé (1008).
- Une clé MCP présentée sur `/api/` est refusée, et inversement (test explicite).

## Contrat de l'API REST (v1)

### `GET /api/v1/activities/{activity_id}/series`

Utilisée par training. `activity_id` : entier positif.

Réponse 200 :
```json
{
  "activity_id": "21234567890",
  "summary": {
    "duration_seconds": 3605,
    "start_time": "2026-11-02T07:01:12Z",
    "end_time": "2026-11-02T08:01:17Z",
    "total_distance_km": 10.42
  },
  "series": {
    "timestamps": ["2026-11-02T07:01:12Z", "..."],
    "power_w": [212, null, "..."],
    "temperature_c": [14, "..."],
    "hr_bpm": [128, "..."],
    "speed_kmh": [10.3, "..."],
    "distance_km": [0.003, "..."],
    "altitude_m": [152.4, "..."],
    "cadence": [172, "..."]
  }
}
```
- Une valeur par enregistrement `record` du FIT, dans l'ordre. Toutes les séries ont la longueur de `timestamps`.
- Une série absente du fichier vaut `null` (pas une liste de `null`). training lève déjà une erreur claire si `power_w` est `null`.
- Horodatages ISO 8601 en UTC avec `Z` (training les lit avec `new Date(t)`).
- `duration_seconds` = dernier - premier timestamp ; `total_distance_km` = dernière distance cumulée, arrondie à 2 décimales.
- `cadence` en pas/min (le FIT donne des cycles/min pour la course : ×2 + `fractional_cadence` si présent).

Erreurs (corps `{"error": "<message>"}`) :

| Code | Cas |
|---|---|
| 400 | `activity_id` invalide |
| 401 | clé absente/fausse |
| 404 | activité inconnue ou pas encore synchronisée |
| 413 | zip > `MAX_FIT_BYTES` |
| 422 | fichier sans FIT exploitable / sans enregistrements |
| 502 | erreur Garmin (message de `GarminError`, sans secret) |

### Plus tard (hors périmètre)

D'autres routes en lecture seule pourront exposer les autres données (sommeil, HRV…) en réutilisant `garmin/official.py`. Ne pas les ajouter tant qu'aucun client n'en a besoin.

## Outils MCP du service regroupé

- Lecture (backend officiel) : les 25 outils actuels de garmin-mcp, noms inchangés.
- Écriture (backend non officiel) : `push_workout`, `list_workouts`, `get_workout`, `schedule_existing_workout`, `move_scheduled_workout`, `get_scheduled_workouts`, noms inchangés.
- `server_health` : un seul, indique en plus si les tokens d'écriture sont présents (`{"status": "ok", "write_tokens": true}`), sans appeler Garmin.
- Instructions FastMCP mises à jour : lecture via l'API officielle, écriture des séances via Garmin Connect.

Points de portage pour l'écriture :
- garminconnect est synchrone : chaque appel passe par `anyio.to_thread.run_sync` pour ne pas bloquer la boucle.
- Le client est créé au premier appel d'un outil d'écriture, pas au démarrage : une panne garminconnect ne doit jamais empêcher la lecture ni le démarrage.
- Tokens absents ou refusés → erreur MCP claire (« lancer `python -m garmin_gateway.login` »), pas de trace.
- Pièges de payload déjà résolus dans garmin-bridge (cibles sur `ExecutableStep`, allure en m/s, floats uniquement) : reprendre `_target_fields` / `_build_steps` tels quels et les couvrir par des tests de caractérisation avant tout nettoyage.

## Conteneur et déploiement

- Base : Dockerfile et compose actuels de garmin-mcp (utilisateur non-root uid 10001, `read_only`, `cap_drop: ALL`, `tmpfs /tmp`, nginx-proxy sur `reseau_interne`).
- Ajouts :
  - `garminconnect[workout]` et un décodeur FIT (`garmin-fit-sdk`, le SDK officiel ; `fitdecode` en repli) dans `requirements.txt`.
  - Volume nommé ou dossier hôte monté sur `/data` (écrit par garminconnect au refresh des tokens) : droits pour l'uid 10001 à documenter (`chown 10001 ./garmin_tokens`).
  - `GARMIN_TOKEN_STORE=/data/garmin_tokens.json` dans le compose.
  - `mem_limit` à revoir (256m → 384m) : décodage FIT d'une longue sortie + garminconnect. Mesurer avant de fixer.
- Les tokens existants de garmin-bridge (`garmin_tokens.json`, format garminconnect 0.3) se copient tels quels dans `/data`.
- Une seule instance.

## CI

- Workflow actuel de garmin-mcp conservé (job `test`, `build_and_push` avec `needs: test`, webhook n8n), image renommée `garmin-gateway`.
- Pas de tag `staging` (le workflow staging de garmin-bridge n'est pas repris).

## Côté training

- `api/src/garmin/client.ts` : URL `${GARMIN_BASE_URL}/api/v1/activities/${id}/series`, en-tête `Authorization: Bearer ${GARMIN_API_KEY}` (déjà le cas). La variable garde son nom, sa valeur devient la `REST_API_KEY` du gateway.
- Ajouter `api/src/garmin/client.test.ts` (fetch simulé) : URL et en-tête envoyés, 200 → objet, 404/502 → erreur avec le code.
- Tester `segmentation.ts` sur une réponse réelle du gateway (fixture JSON tirée du FIT de test) pour valider le contrat de bout en bout.
- `README.md` et `.env.example` : remplacer garmin-bridge par garmin-gateway, décrire la clé REST.

## Étapes et répartition entre agents

Chaque étape suit les règles habituelles : TDD, tests dans Docker, aucune opération git.

| # | Étape | Fichiers principaux | Dépend de | Parallélisable |
|---|---|---|---|---|
| 1 | **Socle** : renommage du paquet, `app.py`, `config.py` (deux clés distinctes, `GARMIN_TOKEN_STORE`), `security.py` par préfixe, `server_health` enrichi, déplacement des outils de lecture dans `mcp/read_tools.py`. Tous les tests existants restent verts. Squelettes vides de `rest/routes.py` et `mcp/workout_tools.py` branchés dans `app.py`. | `config.py`, `security.py`, `app.py`, `mcp/server.py`, `mcp/read_tools.py`, `requirements*.txt` | — | Non (base commune) |
| 2a | **Écriture** : `garmin/unofficial.py`, `mcp/workout_tools.py`, `login.py`, tests de caractérisation du payload, client paresseux en thread. | `garmin/unofficial.py`, `mcp/workout_tools.py`, `login.py`, `tests/test_workout_tools.py` | 1 | Oui, avec 2b et 2c |
| 2b | **API REST + FIT** : `garmin/fit.py`, `rest/routes.py`, fixture FIT, tests de contrat (y compris la réponse JSON de référence `tests/fixtures/series_expected.json`). | `garmin/fit.py`, `rest/routes.py`, `tests/test_fit.py`, `tests/test_rest.py`, `tests/fixtures/` | 1 | Oui, avec 2a et 2c |
| 2c | **training** : `client.ts`, `client.test.ts`, docs. Se base uniquement sur le contrat ci-dessus. | dépôt training | contrat (pas de code) | Oui, avec 2a et 2b |
| 3 | **Intégration** : Dockerfile, compose, `.env.example`, README, CLAUDE.md (proposition seulement), CI, mesure mémoire, test de bout en bout local (conteneur + appel REST + appel MCP). Recopier dans training la fixture `series_expected.json` de 2b pour le test de segmentation. | conteneur, docs, CI | 2a, 2b, 2c | Non |
| 4 | **Bascule** (manuelle, par toi) : renommer le dépôt GitHub `garmin-mcp` en `garmin-gateway`, déployer garmin-gateway à côté, copier les tokens, reconfigurer le connecteur claude.ai (une seule entrée, Bearer `MCP_API_KEY`) et la clé de training, vérifier, puis arrêter garmin-mcp et garmin-bridge et archiver garmin-bridge. | — | 3 | — |

Agents suggérés : 1 agent pour l'étape 1, puis 3 agents en parallèle (2a, 2b, 2c) sans fichier commun, puis 1 agent pour l'étape 3. La seule zone partagée entre 2a et 2b est `requirements.txt` : l'étape 1 y ajoute déjà les deux dépendances.

## Critères de fin

- Tous les tests verts dans Docker et en CI ; le build d'image dépend des tests.
- Un conteneur local répond : `/health` 200 ; `/mcp` avec la clé MCP liste lecture + écriture ; `/api/v1/activities/{id}/series` avec la clé REST renvoie le contrat ; clé croisée → 401.
- Sans fichier de tokens, le service démarre, la lecture et l'API REST fonctionnent, les outils d'écriture renvoient une erreur claire qui indique la commande de login.
- training score une séance réelle via le gateway.

## Hors périmètre

- Autres comptes Garmin / plusieurs instances.
- Fonctionnement de training sans le gateway : le scoring renvoie déjà `scoring_error` sans bloquer l'enregistrement de la séance.
