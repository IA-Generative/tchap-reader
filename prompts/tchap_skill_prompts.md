# Tchap/Matrix Skill pour Open WebUI — Prompts séquentiels

## Contexte technique (à garder en mémoire pour tous les prompts)

Ce projet s'intègre dans un écosystème existant :

- **Open WebUI 0.8.10** avec pipelines Scaleway (type manifold)
- Le function calling passe par le pipeline Scaleway modifié (pas natif OpenWebUI)
- Les tools doivent avoir des `specs` JSON pour être découverts par le LLM
- Les Valves sont des `pydantic.BaseModel` exposées dans le panneau admin OpenWebUI
- Le code du tool doit être un **fichier Python self-contained**, copier-collable dans OpenWebUI → Tools
- Le service backend (comme browser-use) tourne en sidecar Docker/K8s et expose une API REST
- En K8s, le volume OpenWebUI est `emptyDir` — les tools injectés en DB sont perdus au restart → prévoir un script d'injection
- Projet frère de référence : `browser-skill-owui` (même structure)
- Le homeserver Tchap est `matrix.agent.tchap.gouv.fr` (API Matrix standard)
- **V1 : salons non chiffrés uniquement** (E2EE = v2, trop complexe pour le scope initial)

---

# Prompt 1 — Service backend : client Matrix + stockage + API REST

````text
Tu es un ingénieur Python senior expert en protocole Matrix, FastAPI et SQLite.

Génère le service backend "tchap-reader" — un micro-service FastAPI qui se connecte à un homeserver Tchap/Matrix, synchronise les messages d'un salon, les stocke localement, et expose une API REST pour les requêter.

Ce service tourne en sidecar (Docker/K8s) à côté d'Open WebUI, exactement comme le service "browser-use" du projet frère.

--------------------------------------------------
PRIORITÉS
--------------------------------------------------

P0 — le service ne fonctionne pas sans :
- Connexion au homeserver Matrix via access token
- Sync incrémentale via /sync avec persistance de next_batch
- Stockage SQLite des messages textuels
- API REST : healthz, list_rooms, sync_room, get_messages
- Filtrage : ne garder que m.room.message (type m.text)
- Rate limiting configurable (défaut 1 req/s)

P1 — important mais dégradé sans :
- Détection des edits (m.replace) et redactions
- Extraction des reply_to
- Endpoint get_summary (agrège les messages pour le LLM)
- Mode dry-run avec données mock

P2 — bonus :
- Pagination de la sync pour gros historiques
- Métriques Prometheus

--------------------------------------------------
CONFIGURATION (variables d'environnement)
--------------------------------------------------

TCHAP_HOMESERVER_URL=https://matrix.agent.tchap.gouv.fr
TCHAP_ACCESS_TOKEN=<token>
TCHAP_USER_ID=@bot:agent.tchap.gouv.fr
TCHAP_DEVICE_ID=OWUI_BOT
TCHAP_STORE_PATH=/app/data/tchap.db
TCHAP_ALLOWED_ROOM_IDS=!room1:agent.tchap.gouv.fr,!room2:agent.tchap.gouv.fr
TCHAP_DEFAULT_WINDOW_HOURS=168
TCHAP_API_RATE_LIMIT_PER_SEC=1.0
TCHAP_MAX_MESSAGES_PER_ANALYSIS=1000
TCHAP_LOG_LEVEL=INFO

--------------------------------------------------
MODÈLE DE DONNÉES SQLite
--------------------------------------------------

Table : messages
- event_id TEXT PRIMARY KEY
- room_id TEXT NOT NULL
- sender TEXT NOT NULL
- timestamp INTEGER NOT NULL
- body TEXT NOT NULL
- event_type TEXT DEFAULT 'm.text'
- reply_to_event_id TEXT
- is_edit BOOLEAN DEFAULT FALSE
- replaces_event_id TEXT
- is_redacted BOOLEAN DEFAULT FALSE
- synced_at INTEGER NOT NULL

Table : sync_state
- room_id TEXT PRIMARY KEY
- next_batch TEXT
- last_synced_at INTEGER

Index : idx_messages_room_ts ON messages(room_id, timestamp)

--------------------------------------------------
API REST
--------------------------------------------------

GET /healthz
→ {"status": "healthy", "rooms_tracked": 2, "total_messages": 1234}

GET /rooms
→ [{"room_id": "!abc:server", "name": "Salon X", "message_count": 456, "last_synced": "2026-03-22T10:00:00Z"}]

POST /sync
Body: {"room_id": "!abc:server"}
→ {"synced": 123, "new_messages": 45, "next_batch": "s123_456"}
Lance une sync incrémentale pour le salon. Rate-limité.

POST /messages
Body: {"room_id": "!abc:server", "since_hours": 168, "limit": 1000}
→ {"messages": [...], "total": 456, "window_start": "...", "window_end": "..."}
Retourne les messages stockés localement, filtrés par fenêtre temporelle.

POST /summary
Body: {"room_id": "!abc:server", "since_hours": 168, "max_messages": 500}
→ {"room_name": "...", "period": "...", "message_count": 234, "top_senders": [...], "messages_for_llm": "..."}
Prépare un bloc de texte compact pour le LLM :
- Pseudonymise les expéditeurs (Utilisateur_1, Utilisateur_2...)
- Formate : "[timestamp] Utilisateur_X: message"
- Tronque si nécessaire pour rester sous max_messages
- Inclut des stats agrégées (nb messages, top senders pseudonymisés, répartition horaire)

--------------------------------------------------
CLIENT MATRIX
--------------------------------------------------

Utiliser httpx (pas matrix-nio — trop lourd pour du read-only non-E2EE).

Endpoints Matrix utilisés :
- GET /_matrix/client/v3/sync?since={next_batch}&timeout=0&filter={filter}
  filter : {"room": {"rooms": [allowed_rooms], "timeline": {"types": ["m.room.message"], "limit": 100}}}
- GET /_matrix/client/v3/joined_rooms
- GET /_matrix/client/v3/rooms/{room_id}/state/m.room.name

Headers : Authorization: Bearer {access_token}

Gestion des erreurs :
- 429 Too Many Requests → respecter Retry-After, backoff exponentiel
- 401 → log erreur, ne pas retenter
- Timeout → retry 2 fois max avec backoff
- Ne jamais logger le contenu des messages en INFO, seulement en DEBUG

--------------------------------------------------
SÉCURITÉ
--------------------------------------------------

- Secrets uniquement via env vars
- Allowlist stricte des room_ids
- Refuser toute requête sur un room_id non autorisé
- Pseudonymisation par défaut dans /summary
- Taille max de fenêtre temporelle : 30 jours
- Max messages par requête : configurable (défaut 1000)

--------------------------------------------------
STRUCTURE DU PROJET
--------------------------------------------------

tchap-reader/
├── app/
│   ├── __init__.py
│   ├── config.py          # Pydantic Settings
│   ├── models.py          # Pydantic models pour l'API
│   ├── database.py        # SQLite repository
│   ├── matrix_client.py   # Client Matrix HTTP
│   ├── sync_service.py    # Logique de sync incrémentale
│   ├── summary_service.py # Préparation des données pour le LLM
│   ├── api.py             # Routes FastAPI
│   └── main.py            # App factory
├── requirements.txt
├── .env.example
└── Dockerfile

--------------------------------------------------
QUALITÉ
--------------------------------------------------

- Python 3.11+
- Type hints partout
- Docstrings sur les fonctions publiques
- Structured logging (pas de print)
- Async/await
- Pas de bare except
````

---

# Prompt 2 — Tool OpenWebUI + intégration LLM

````text
Tu es un ingénieur Python senior expert en Open WebUI et en prompt engineering.

Tu continues un projet commencé au prompt précédent. Le service backend "tchap-reader" existe et expose une API REST (healthz, rooms, sync, messages, summary). Tu dois maintenant créer le Tool Open WebUI qui appelle ce service.

Ce Tool sera copié-collé dans Open WebUI → Workspace → Tools → Create Tool.

--------------------------------------------------
CONTRAINTES OPEN WEBUI 0.8.x
--------------------------------------------------

- Le Tool est une classe `Tools` avec une inner class `Valves(BaseModel)`
- Chaque méthode async publique devient un outil appelable par le LLM
- Paramètres spéciaux disponibles : __event_emitter__, __messages__, __user__
- Le fichier doit être self-contained (imports dans les méthodes si nécessaire)
- Les docstrings des méthodes sont utilisées comme description pour le function calling
- Limiter à 3 méthodes max (le LLM se perd avec trop de choix)

--------------------------------------------------
TOOL : 3 MÉTHODES
--------------------------------------------------

1. tchap_rooms() → str
   Liste les salons disponibles et leur activité récente.
   Appelle GET {base_url}/rooms

2. tchap_analyze(room_id: str, question: str = "", since_hours: int = 168) → str
   Méthode principale tout-en-un :
   - Appelle POST {base_url}/sync pour rafraîchir
   - Appelle POST {base_url}/summary pour obtenir les données
   - Si question est vide : retourne le résumé structuré
   - Si question est fournie : retourne les données + la question pour que le LLM y réponde
   Le résultat doit être structuré en sections Markdown :
   - Résumé exécutif
   - Statistiques (nb messages, période, top contributeurs pseudonymisés)
   - Données brutes compactes pour analyse LLM
   - Instruction au LLM selon le type de question

3. tchap_sync(room_id: str) → str
   Force une synchronisation manuelle.
   Appelle POST {base_url}/sync

--------------------------------------------------
VALVES
--------------------------------------------------

class Valves(BaseModel):
    base_url: str = Field(default="http://host.docker.internal:8087", description="URL du service tchap-reader")
    timeout: int = Field(default=120, description="Timeout en secondes")
    default_since_hours: int = Field(default=168, description="Fenêtre temporelle par défaut (heures)")

--------------------------------------------------
COMPORTEMENT D'ANALYSE
--------------------------------------------------

Quand tchap_analyze est appelé :
1. Sync le salon (ignorer les erreurs de sync — utiliser les données locales)
2. Récupérer le summary
3. Construire un prompt structuré pour le LLM avec les données :

Si pas de question spécifique, le prompt doit demander au LLM de produire :
- Résumé exécutif (3-5 phrases)
- Thèmes dominants (liste numérotée)
- Irritants principaux (liste avec contexte)
- Demandes d'action explicites
- Signaux faibles / points de vigilance
- Synthèse "À retenir / À faire / À surveiller"

Si une question est posée, le prompt doit :
- Fournir les données du salon comme contexte
- Poser la question de l'utilisateur
- Demander une réponse structurée avec citations pseudonymisées

Important :
- Toujours répondre en français
- Pseudonymiser par défaut
- Ne pas renvoyer plus de 3000 tokens de messages bruts au LLM
- Préférer les extraits représentatifs aux dumps complets

--------------------------------------------------
FICHIER
--------------------------------------------------

Générer UN seul fichier : app/openwebui_tchap_tool.py
Self-contained, prêt à copier dans Open WebUI.

--------------------------------------------------
EXEMPLES D'USAGE
--------------------------------------------------

Utilisateur : "Quels sont les salons Tchap disponibles ?"
→ LLM appelle tchap_rooms()

Utilisateur : "Fais-moi une synthèse du salon X sur les 7 derniers jours"
→ LLM appelle tchap_analyze(room_id="!abc:server", since_hours=168)

Utilisateur : "Quels sont les irritants remontés cette semaine dans le salon Y ?"
→ LLM appelle tchap_analyze(room_id="!xyz:server", question="Quels sont les irritants remontés ?", since_hours=168)

Utilisateur : "Compare les tendances des salons X et Y"
→ LLM appelle tchap_analyze deux fois, puis synthétise
````

---

# Prompt 3 — Tests, Docker, déploiement, README

````text
Tu es un ingénieur DevOps/QA senior.

Tu complètes le projet tchap-reader dont le service backend et le tool Open WebUI sont déjà implémentés. Tu dois maintenant créer : les tests, le Docker, les scripts de déploiement et le README.

Ne régénère PAS le code applicatif.

--------------------------------------------------
TESTS
--------------------------------------------------

Framework : pytest + pytest-asyncio + httpx

tests/
├── __init__.py
├── conftest.py           # Fixtures : mock Matrix server, test DB
├── test_database.py      # CRUD SQLite, déduplication, fenêtres temporelles
├── test_matrix_client.py # Appels Matrix mockés, gestion 429, retry
├── test_sync_service.py  # Sync incrémentale, next_batch, filtrage
├── test_summary_service.py # Pseudonymisation, troncature, formatage
├── test_api.py           # Endpoints FastAPI via TestClient
└── mock_data.py          # Données Matrix de test réalistes

Fixtures mock Matrix :
- Réponse /sync avec timeline de 10 messages
- Réponse /sync vide (pas de nouveaux messages)
- Réponse /sync avec edits et redactions
- Réponse 429 avec Retry-After
- Réponse 401 unauthorized
- Réponse /joined_rooms
- Réponse /rooms/{id}/state/m.room.name

Tests critiques :
- Allowlist respectée (room_id non autorisé → 403)
- Pseudonymisation effective (aucun @user:server dans la sortie)
- Déduplication (même event_id pas inséré deux fois)
- Fenêtre temporelle respectée
- Rate limiting respecté
- next_batch correctement persisté et réutilisé

--------------------------------------------------
DOCKER
--------------------------------------------------

Dockerfile :
- Base : python:3.11-slim
- Utilisateur non-root
- Volume pour /app/data (SQLite + sync state)
- Healthcheck sur /healthz
- CMD : uvicorn app.main:app --host 0.0.0.0 --port 8087

docker-compose.yaml (dev local, s'intègre avec le stack grafrag) :
- Service tchap-reader sur port 8087
- Volume persistant pour la DB SQLite
- env_file: .env
- Réseau : se connecter au réseau grafrag

--------------------------------------------------
SCRIPTS DE DÉPLOIEMENT
--------------------------------------------------

scripts/
├── deploy_docker.sh       # Build + run local
├── deploy_k8s.sh          # Build + push + apply manifests K8s
├── register_openwebui.sh  # Injecter le tool dans la DB OpenWebUI
└── run_tests.sh           # Lancer les tests

Le script register_openwebui.sh doit :
1. Copier le fichier openwebui_tchap_tool.py
2. Remplacer les URLs pour le contexte (Docker vs K8s)
3. Générer les specs JSON à partir du code
4. Insérer/mettre à jour dans la DB OpenWebUI (tool + specs)
5. Fonctionner en local (Docker) et en K8s (kubectl exec)

--------------------------------------------------
MANIFESTS K8S
--------------------------------------------------

k8s/
├── deployment.yaml    # 1 replica, resources requests/limits, probes
├── service.yaml       # ClusterIP port 8087
├── pvc.yaml           # PersistentVolumeClaim pour la DB SQLite (important !)
└── configmap.yaml     # Config non-secrète

Le PVC est critique : contrairement à browser-use (stateless), tchap-reader a un état (la DB SQLite avec les messages sync). Il ne doit PAS utiliser emptyDir.

--------------------------------------------------
README.md
--------------------------------------------------

Structure :
1. Description du projet (1 paragraphe)
2. Architecture (schéma texte : OpenWebUI → Tool → tchap-reader → Matrix API → Tchap)
3. Prérequis (compte bot Tchap, token, room IDs)
4. Installation locale (docker-compose)
5. Configuration (.env.example documenté)
6. Déploiement K8s
7. Intégration Open WebUI (copier le tool)
8. Exemples d'usage (captures texte de conversations OpenWebUI)
9. Sécurité et conformité
   - Pseudonymisation
   - Allowlist
   - Pas de logging de contenu
   - Pas d'inférence RH
10. Limitations connues
    - Pas de E2EE (v1)
    - Sync lente sur gros historiques
    - Qualité de l'analyse dépend du LLM
11. Évolutions v2
    - E2EE via matrix-nio + libolm
    - Analyse multi-salons
    - Cache LLM des analyses
    - Webhooks pour sync temps réel

--------------------------------------------------
FICHIERS À GÉNÉRER
--------------------------------------------------

tchap-reader/
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── mock_data.py
│   ├── test_database.py
│   ├── test_matrix_client.py
│   ├── test_sync_service.py
│   ├── test_summary_service.py
│   └── test_api.py
├── scripts/
│   ├── deploy_docker.sh
│   ├── deploy_k8s.sh
│   ├── register_openwebui.sh
│   └── run_tests.sh
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── pvc.yaml
│   └── configmap.yaml
├── docker-compose.yaml
├── Dockerfile
├── pytest.ini
├── requirements-test.txt
└── README.md

Ne régénère PAS les fichiers du prompt 1 et 2.
````
