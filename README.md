# garmin-gateway

Service unique d'accès aux données Garmin d'un compte, avec deux portes :

- **MCP** (`/mcp`, pour claude.ai et les clients MCP) :
  - **lecture** des activités, de la santé et du bien-être via l'**API officielle Garmin Connect Personal**
    (`https://personal.connectapi.garmin.com`, spec dans `spec/openapi.json`) ;
  - **écriture des séances** (création, planification, déplacement) via l'API non officielle Garmin Connect
    (`garminconnect[workout]`).
- **API REST en lecture seule** (`/api/v1/...`, pour les clients non-MCP, d'abord training) : séries seconde par seconde
  d'une activité, décodées depuis son fichier FIT.

`GET /health` est ouvert (healthcheck Docker). Tout autre chemin répond 404.

## Les deux clés

| Clé | Porte | Utilisée par |
|---|---|---|
| `MCP_API_KEY` | `/mcp` uniquement | le connecteur claude.ai / le client MCP |
| `REST_API_KEY` | `/api/` uniquement | training (sa variable `GARMIN_API_KEY`) |

- Chacune fait ≥ 32 caractères et elles doivent être **différentes** (le service refuse de démarrer sinon) :
  `openssl rand -hex 32` pour chacune.
- Une clé ne s'envoie que dans `Authorization: Bearer <clé>` ; une clé dans l'URL (`?api_key=`) est refusée.
  Comparaison sur les octets bruts, en temps constant.
- Une clé présentée sur l'autre porte reçoit un 401 (corps `{"error": "Unauthorized"}`, en-tête `WWW-Authenticate: Bearer`).
- L'API REST n'a que des routes `GET` : elle est en lecture seule par construction. Le MCP a tout (lecture + écriture).

## Fonctionnement

- **Lecture (API officielle)** : la clé personnelle Garmin (`GARMIN_PERSONAL_API_KEY`) est échangée contre un access token
  (`POST /personalapitoken-service/personal-api/personal-api-key/token`). Le token est gardé en mémoire, renouvelé 30 s avant
  son expiration, et redemandé une fois si Garmin répond 401. Ni MFA ni risque de blocage du compte.
- **Écriture (Garmin Connect)** : le client garminconnect est créé au **premier appel** d'un outil d'écriture, jamais au
  démarrage, à partir du fichier de tokens `GARMIN_TOKEN_STORE`. Sans ce fichier, ou si Garmin refuse les tokens, le service
  démarre quand même, la lecture et l'API REST fonctionnent, et les outils d'écriture renvoient une erreur qui indique la
  commande de login. garminconnect renouvelle les tokens seul et réécrit le fichier (d'où le volume `/data` en écriture).
  Les appels garminconnect (bloquants) tournent dans un thread.
- Transport MCP streamable HTTP (stateless), port interne 8001.

## Outils MCP

33 outils. Les dates sont au format `YYYY-MM-DD` et les plages sont inclusives.

**Lecture** (API officielle, 26 outils) :

| Domaine | Outils | Plage max |
|---|---|---|
| Activités | `list_activities`, `get_activity`, `get_activity_workouts`, `get_activity_file`, `get_wellness_activity_summaries` | 366 j |
| Résumés quotidiens | `get_daily_summary`, `get_daily_summaries` | 366 j |
| Pas, stress | `get_steps`, `get_stress` | 28 j |
| Hydratation, sommeil, Body Battery | `get_hydration`, `get_sleep`, `get_sleep_summaries`, `get_body_battery` | 31 j |
| HRV, FC, SpO2 | `get_hrv`, `get_hrv_summaries`, `get_heart_rate`, `get_spo2` | 366 j |
| Poids | `get_weight`, `get_weight_entries`, `get_weight_goals` | 366 j |
| Profil | `get_fitness_age`, `get_heart_rate_zones`, `get_user_settings` | — |
| Nutrition, cycle | `get_nutrition_log`, `get_menstrual_cycle_summary`, `get_menstrual_cycle_day` | — |

`get_activity_file` renvoie le fichier d'origine (zip contenant le FIT) en base64, dans la limite de `MAX_FILE_BYTES`.

**Écriture des séances** (Garmin Connect, 6 outils) : `push_workout` (crée une séance de course et la planifie à une date),
`list_workouts`, `get_workout`, `schedule_existing_workout`, `move_scheduled_workout`, `get_scheduled_workouts`.

**`server_health`** : `{"status": "ok", "write_tokens": true|false}` — indique si le fichier de tokens d'écriture est présent,
sans appeler Garmin.

Les erreurs Garmin reviennent au client MCP comme erreurs d'outil (`isError`), avec un message court et sans secret.

## API REST (v1)

### `GET /api/v1/activities/{activity_id}/series`

`activity_id` : entier positif. Le service télécharge le fichier de l'activité (`/download-service/files/activity/{id}`, zip),
en extrait le FIT et renvoie :

```json
{
  "activity_id": "21234567890",
  "summary": {"duration_seconds": 3605, "start_time": "2026-11-02T07:01:12Z", "end_time": "2026-11-02T08:01:17Z", "total_distance_km": 10.42},
  "series": {
    "timestamps": ["2026-11-02T07:01:12Z", "..."],
    "power_w": [212, null], "temperature_c": [14], "hr_bpm": [128], "speed_kmh": [10.3],
    "distance_km": [0.003], "altitude_m": [152.4], "cadence": [172]
  }
}
```

- Une valeur par enregistrement `record` du FIT, dans l'ordre ; toutes les séries ont la longueur de `timestamps`.
- Valeur manquante dans un enregistrement → `null` à son index ; série absente de tout le fichier → `null` entier.
- Horodatages ISO 8601 UTC en `Z`. `duration_seconds` = dernier − premier horodatage ; `total_distance_km` = dernière distance
  cumulée (2 décimales). `cadence` en pas/min. Arrondis détaillés dans `src/garmin_gateway/garmin/fit.py`.
- Exemple complet de réponse : `tests/fixtures/series_expected.json`.

Erreurs (corps `{"error": "<message>"}`, jamais de secret) :

| Code | Cas |
|---|---|
| 400 | `activity_id` invalide |
| 401 | clé absente ou fausse (dont la clé MCP) |
| 404 | activité inconnue ou pas encore synchronisée |
| 413 | zip > `MAX_FIT_BYTES`, ou FIT décompressé trop gros |
| 422 | fichier sans FIT exploitable ou sans enregistrements |
| 502 | erreur Garmin (clé personnelle refusée, rate limit, API injoignable…) |

## Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `MCP_API_KEY` | requis | Clé de `/mcp` (≥ 32 caractères) |
| `REST_API_KEY` | requis | Clé de `/api/` (≥ 32 caractères, différente de `MCP_API_KEY`) |
| `GARMIN_PERSONAL_API_KEY` | requis | Clé personnelle Garmin Connect (elle expire) : outils de lecture et API REST |
| `DOMAIN` | requis | Hôte public accepté (`Host`/`Origin` du MCP ; docker-compose le remplit avec `VIRTUAL_HOST`) |
| `GARMIN_TOKEN_STORE` | `/data/garmin_tokens.json` | Fichier de tokens garminconnect (écriture) ; peut être absent au démarrage |
| `PORT` | `8001` | Port d'écoute |
| `HTTP_TIMEOUT` | `30` | Timeout des appels à l'API officielle (s) |
| `MAX_FILE_BYTES` | `5242880` | Taille max du fichier renvoyé par `get_activity_file` (octets, avant base64) |
| `MAX_FIT_BYTES` | `20971520` | Taille max du zip téléchargé par l'API REST (octets) |
| `GARMIN_BASE_URL` | `https://personal.connectapi.garmin.com` | URL de l'API officielle |

## Déploiement

```bash
cp .env.example .env            # VIRTUAL_HOST, MCP_API_KEY, REST_API_KEY, GARMIN_PERSONAL_API_KEY
mkdir -p garmin_tokens && chmod 700 garmin_tokens
docker compose up -d
```

- Client MCP : `https://<VIRTUAL_HOST>/mcp`, en-tête `Authorization: Bearer <MCP_API_KEY>`.
- training : `GARMIN_BASE_URL=https://<VIRTUAL_HOST>`, `GARMIN_API_KEY=<REST_API_KEY>`.
- Aucun port publié sur l'hôte : accès via nginx-proxy sur le réseau externe `reseau_interne`.

### Tokens d'écriture (Garmin Connect)

Le dossier hôte `./garmin_tokens` est monté sur `/data`. Le conteneur tourne avec l'uid/gid `PUID`/`PGID` (défaut
`1000:1000`) : ce dossier et son contenu appartiennent donc à l'utilisateur de l'hôte, qui les manipule sans `sudo`.
Deux façons d'y mettre `garmin_tokens.json` :

- **Reprendre les tokens existants** (format garminconnect 0.3, ceux de garmin-bridge par exemple) :
  ```bash
  cp /chemin/vers/garmin-bridge/garmin_tokens.json garmin_tokens/garmin_tokens.json
  chmod 600 garmin_tokens/garmin_tokens.json
  ```
- **Premier login** dans le conteneur lancé (e-mail, mot de passe et code MFA demandés au terminal) :
  ```bash
  docker exec -it garmin-gateway python -m garmin_gateway.login
  ```
  À refaire si un outil d'écriture répond « Garmin Connect rejected the saved tokens ».

Pas besoin de redémarrer : le prochain appel d'un outil d'écriture lit le fichier. `server_health` doit alors renvoyer
`"write_tokens": true`.

`GARMIN_PERSONAL_API_KEY` expire (durée choisie à la création) : en cas d'erreur « Garmin rejected the personal API key », en
générer une nouvelle sur Garmin Connect web, mettre à jour `.env` et relancer le conteneur.

## Sécurité du conteneur

- Validation `Host` / `Origin` sur `DOMAIN` pour `/mcp` (anti DNS rebinding) : autre hôte → 421, autre origine → 403.
- Websockets refusés. Logs d'accès uvicorn désactivés.
- Utilisateur non-root (`PUID`/`PGID`, défaut 1000), FS en lecture seule (seuls `/tmp` en tmpfs et `/data` sont inscriptibles), `cap_drop: ALL`,
  `no-new-privileges`, limites mémoire (384 Mo) et PIDs.

## CI

`.github/workflows/docker.yml`, à chaque push sur `main` ou sur un tag `v*` :

1. lance les tests (`pytest`, Python 3.12) ; l'image n'est pas publiée s'ils échouent (`needs: test`) ;
2. build et pousse `<DOCKERHUB_USERNAME>/garmin-gateway:latest` et `:<branche ou tag>` (`:main` ou `:v1.2.3`) ;
3. appelle le webhook n8n de redéploiement.

Secrets requis : `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, `N8N_WEBHOOK_ID`.

## Tests

Dans Docker, depuis la racine du dépôt :

```bash
docker run --rm -v "$PWD":/src -w /src -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"
```

Les tests n'appellent jamais Garmin (transports simulés, faux client garminconnect), n'ont besoin d'aucune vraie clé et
ignorent les variables d'environnement du shell. Les fixtures FIT (`tests/fixtures/*.zip`) et la réponse de référence
`series_expected.json` se régénèrent avec `python tests/fixtures/make_fit.py` (même commande Docker).

Vérification rapide d'un conteneur lancé (liste des outils) :

```bash
curl -s https://<VIRTUAL_HOST>/mcp \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
