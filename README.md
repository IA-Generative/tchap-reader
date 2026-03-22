# Tchap Reader — Analyse de salons Matrix/Tchap pour Open WebUI

Service de lecture et d'analyse de salons Tchap (protocole Matrix) intégré à Open WebUI. Permet de produire des synthèses, détecter les tendances, identifier les irritants et répondre à des questions sur le contenu d'un salon.

## Architecture

```
Utilisateur (Open WebUI)
    │
    ▼
┌─────────────────────────────────┐
│  Tool OpenWebUI (tchap_analyze) │
│  openwebui_tchap_tool.py        │
└──────────┬──────────────────────┘
           │ HTTP
           ▼
┌─────────────────────────────────┐
│  tchap-reader (FastAPI)         │
│  /sync → /summary → /messages  │
└──────────┬──────────────────────┘
           │ Matrix Client API
           ▼
┌─────────────────────────────────┐
│  Homeserver Tchap               │
│  matrix.agent.tchap.gouv.fr     │
└─────────────────────────────────┘
```

## Prérequis

- Un **compte bot Tchap** avec un access token
- Le bot doit être **invité dans les salons** à analyser
- Les room IDs des salons (format `!xxx:agent.tchap.gouv.fr`)
- Docker ou un cluster Kubernetes

## Installation locale

```bash
# 1. Configurer
cp .env.example .env
# Éditer .env avec les credentials Tchap

# 2. Lancer
docker compose up -d

# 3. Vérifier
curl http://localhost:8087/healthz
```

## Configuration

| Variable | Requis | Défaut | Description |
|----------|--------|--------|-------------|
| `TCHAP_HOMESERVER_URL` | Non | `https://matrix.agent.tchap.gouv.fr` | URL du homeserver |
| `TCHAP_ACCESS_TOKEN` | **Oui** | - | Token d'accès du bot |
| `TCHAP_USER_ID` | **Oui** | - | ID Matrix du bot |
| `TCHAP_ALLOWED_ROOM_IDS` | **Oui** | - | Room IDs autorisés (virgule) |
| `TCHAP_STORE_PATH` | Non | `/app/data/tchap.db` | Chemin SQLite |
| `TCHAP_DEFAULT_WINDOW_HOURS` | Non | `168` | Fenêtre par défaut (7j) |
| `TCHAP_API_RATE_LIMIT_PER_SEC` | Non | `1.0` | Rate limit Matrix |
| `TCHAP_MAX_MESSAGES_PER_ANALYSIS` | Non | `1000` | Max messages par analyse |
| `TCHAP_MAX_WINDOW_DAYS` | Non | `30` | Fenêtre max |
| `TCHAP_ANONYMIZE_OUTPUT` | Non | `true` | Pseudonymiser les noms |
| `TCHAP_LOG_LEVEL` | Non | `INFO` | Niveau de log |

## Déploiement Kubernetes

```bash
# Appliquer les manifests
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

**Important** : contrairement à browser-use (stateless), tchap-reader utilise un **PVC** pour persister la base SQLite (messages synchronisés, next_batch).

## Intégration Open WebUI

1. Ouvrir **Workspace → Tools → Create Tool**
2. Copier-coller le contenu de `app/openwebui_tchap_tool.py`
3. Configurer les **Valves** (URL du service)
4. Le tool expose 3 méthodes :
   - `tchap_rooms()` — lister les salons
   - `tchap_analyze(room_id, question, since_hours)` — analyse complète
   - `tchap_sync(room_id)` — forcer la synchronisation

## Exemples d'usage

```
"Quels sont les salons Tchap disponibles ?"
→ Appelle tchap_rooms()

"Fais-moi une synthèse du salon X sur les 7 derniers jours"
→ Appelle tchap_analyze(room_id, since_hours=168)

"Quels sont les irritants remontés cette semaine ?"
→ Appelle tchap_analyze(room_id, question="Quels sont les irritants ?")

"Quelles tendances depuis 30 jours ?"
→ Appelle tchap_analyze(room_id, question="Quelles tendances ?", since_hours=720)
```

## Sécurité et conformité

- **Pseudonymisation** : les noms sont remplacés par Utilisateur_1, Utilisateur_2... dans toutes les sorties
- **Allowlist** : seuls les salons explicitement autorisés sont accessibles
- **Pas de logging de contenu** : les messages ne sont jamais loggés en INFO
- **Secrets en env vars** : aucun secret dans le code
- **Pas d'inférence RH** : pas de sentiment analysis sur les personnes, uniquement des catégories métier (irritant, demande, blocage, etc.)
- **Fenêtre temporelle limitée** : max 30 jours configurable

## Limitations (v1)

- **Pas de E2EE** : seuls les salons non chiffrés sont supportés
- **Sync lente** : la première sync d'un gros salon peut prendre du temps
- **Qualité de l'analyse** : dépend du modèle LLM utilisé
- **Pas de temps réel** : sync manuelle ou sur demande (pas de webhook)
- **SQLite** : mono-instance, pas de scaling horizontal

## Évolutions v2

- E2EE via `matrix-nio` + `libolm`
- Analyse multi-salons comparative
- Cache LLM des analyses (éviter de re-analyser les mêmes messages)
- Webhook/SSE pour sync temps réel
- Interface de configuration des salons dans OpenWebUI
