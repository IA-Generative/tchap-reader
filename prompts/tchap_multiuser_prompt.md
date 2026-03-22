# Prompt — Tchap Reader : accès multi-utilisateur, groupes et configuration interactive

## Contexte technique

Ce prompt fait évoluer le projet **tchap-reader** existant (service FastAPI + tool OpenWebUI) vers un modèle **multi-tenant** avec gestion fine des droits.

### Ce qui existe déjà

- Service backend FastAPI (`tchap-reader`) sur port 8087
- SQLite pour stocker messages, sync_state, config
- Client Matrix HTTP (httpx, pas matrix-nio)
- Tool OpenWebUI `tchap_reader` (3 méthodes : rooms, analyze, sync)
- Tool OpenWebUI `tchap_admin` (3 méthodes : status, configure, discover_and_follow)
- Déploiement Docker et K8s (PVC pour la DB)
- Enregistrement automatique via manifest.yaml + register_all_openwebui_tools.py

### Stack

- Open WebUI 0.8.10 avec Keycloak SSO
- Pipelines Scaleway (manifold) avec function calling
- Groupes OpenWebUI existants (`/api/v1/groups`)
- Homeserver Tchap : `matrix.agent.tchap.gouv.fr`
- Python 3.11+, FastAPI, SQLite, httpx

### Paramètres OpenWebUI disponibles dans les tools

Le tool reçoit `__user__` qui contient :
```python
{
    "id": "uuid",
    "email": "prenom.nom@interieur.gouv.fr",
    "name": "Prénom Nom",
    "role": "admin" | "user",
    "token": "jwt..."  # JWT OpenWebUI pour appeler les API internes
}
```

---

## Architecture cible

### 3 modes d'accès aux salons

| Mode | Compte Matrix utilisé | Qui configure | Qui accède | Cas d'usage |
|------|----------------------|---------------|------------|-------------|
| **Individuel** | Le propre compte Tchap de l'utilisateur | L'utilisateur lui-même | Lui seul | Agent qui veut analyser ses salons personnels |
| **Groupe** | Compte de service (bot) | Un membre du groupe ayant les droits de gestion du tool | Les membres du groupe OpenWebUI | Équipe projet avec un salon partagé |
| **Global** | Compte de service (bot) | Admin plateforme | Tous les utilisateurs | Salon institutionnel ouvert à tous |

### Stockage multi-tenant (une seule DB)

Toutes les données dans une seule base SQLite avec des colonnes de filtrage :

```sql
-- Qui possède quel accès Matrix
CREATE TABLE IF NOT EXISTS matrix_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL CHECK(owner_type IN ('user', 'group', 'global')),
    owner_id TEXT NOT NULL,          -- user UUID, group UUID, ou 'global'
    homeserver_url TEXT NOT NULL,
    user_id TEXT NOT NULL,           -- Matrix user ID
    access_token TEXT NOT NULL,      -- Token Matrix (à stocker chiffré en v2)
    device_id TEXT DEFAULT 'OWUI_BOT',
    configured_by TEXT NOT NULL,     -- UUID de l'utilisateur qui a configuré
    created_at INTEGER NOT NULL,
    UNIQUE(owner_type, owner_id)
);

-- Quels salons sont suivis, par quel owner
CREATE TABLE IF NOT EXISTS followed_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    room_name TEXT DEFAULT '',
    added_by TEXT NOT NULL,          -- UUID de l'utilisateur qui a ajouté
    created_at INTEGER NOT NULL,
    UNIQUE(owner_type, owner_id, room_id)
);

-- Messages indexés par room (existant, ajouter owner)
-- Ajouter à la table messages existante :
--   owner_type TEXT NOT NULL
--   owner_id TEXT NOT NULL
-- Index: idx_messages_owner ON messages(owner_type, owner_id, room_id, timestamp)

-- Sync state par owner+room
-- Modifier sync_state :
--   PRIMARY KEY (owner_type, owner_id, room_id)
```

### Matrice de droits

| Action | Rôle requis | Condition supplémentaire |
|--------|-------------|--------------------------|
| Configurer son propre compte Tchap | user | — |
| Suivre un salon en mode individuel | user | Utilise son propre compte Matrix |
| Configurer un accès groupe | user | Doit être membre d'un groupe OpenWebUI ayant les droits de gestion du tool |
| Configurer un accès global | admin | — |
| Voir ses salons personnels | user | — |
| Voir les salons de ses groupes | user | Membre du groupe |
| Voir tous les salons de la plateforme | admin | — |
| Révoquer l'accès d'un utilisateur dans un groupe | admin | — |
| Analyser un salon | user | A accès via individuel, groupe ou global |

Pour vérifier l'appartenance à un groupe, le service doit appeler l'API OpenWebUI :
```
GET /api/v1/groups/
Authorization: Bearer {user_jwt}
```
et vérifier que l'utilisateur est membre du groupe cible.

---

## Configuration interactive avec Tchap/Matrix

### Flow de connexion — Mode individuel

Le tool guide l'utilisateur pas à pas dans le chat :

```
Utilisateur : "Configure mon accès Tchap"

Tool (tchap_setup) :
  → Appelle /admin/setup/start avec __user__
  → Retourne :

"## Configuration de votre accès Tchap

Choisissez votre méthode de connexion :

1. **SSO / France Connect Agent** — Connexion via le navigateur (recommandé)
2. **Email et mot de passe** — Connexion directe
3. **Token d'accès** — Si vous avez déjà un token Matrix

Répondez 1, 2 ou 3."
```

#### Option 1 — SSO (flow OIDC via Tchap)

```
Tool :
  → Appelle POST /admin/setup/sso-start
  → Le backend appelle GET /_matrix/client/v3/login pour lister les flows
  → Si SSO disponible : génère une URL de redirect

"Cliquez sur ce lien pour vous connecter via votre SSO :

[🔐 Se connecter à Tchap](https://matrix.agent.tchap.gouv.fr/_matrix/client/v3/login/sso/redirect?redirectUrl=...)

Une fois connecté, revenez ici et tapez 'ok'."

Utilisateur : "ok"

Tool :
  → Appelle POST /admin/setup/sso-complete
  → Le backend vérifie le token reçu via le callback
  → Sauvegarde le token

"Connecté ✓ Compte : @prenom.nom-interieur.gouv.fr:agent.tchap.gouv.fr"
```

**Implémentation SSO côté backend :**
1. `POST /admin/setup/sso-start` :
   - Appelle `GET /_matrix/client/v3/login` sur le homeserver
   - Vérifie que `m.login.sso` est dans les flows supportés
   - Génère un `state` unique (UUID), le stocke en DB temporairement
   - Construit l'URL de redirect : `{homeserver}/_matrix/client/v3/login/sso/redirect?redirectUrl={callback_url}`
   - Le `callback_url` pointe vers le service tchap-reader : `http://tchap-reader:8087/admin/setup/sso-callback?state={state}`
   - Retourne l'URL au tool

2. `GET /admin/setup/sso-callback?loginToken={token}&state={state}` :
   - Reçoit le `loginToken` de Matrix après le SSO
   - Appelle `POST /_matrix/client/v3/login` avec `{"type": "m.login.token", "token": loginToken}`
   - Reçoit un `access_token` et un `user_id`
   - Stocke dans `matrix_accounts` avec le `state` pour retrouver le owner

3. `POST /admin/setup/sso-complete` :
   - Vérifie que le callback a bien été reçu pour ce `state`
   - Retourne le résultat

**Note :** le callback SSO nécessite que le service tchap-reader soit accessible via une URL publique (ingress K8s ou port-forward). Si ce n'est pas possible, fallback sur l'option 2 ou 3.

#### Option 2 — Email / mot de passe

```
Tool :
  → Retourne : "Quel est votre email Tchap ?"

Utilisateur : "prenom.nom@interieur.gouv.fr"

Tool :
  → Retourne : "Mot de passe ?"
  → (Note : le mot de passe transite par le chat. Avertir l'utilisateur.)

"⚠️ Votre mot de passe transitera par le chat. Pour plus de sécurité,
utilisez plutôt la méthode SSO (option 1) ou générez un token dans
Element (option 3).

Tapez votre mot de passe ou 'annuler' :"

Utilisateur : "monmotdepasse"

Tool :
  → Appelle POST /admin/setup/login-password
  → Le backend appelle POST /_matrix/client/v3/login
    avec {"type": "m.login.password", "identifier": {"type": "m.id.thirdparty", "medium": "email", "address": email}, "password": password}
  → Stocke le token, NE stocke PAS le mot de passe

"Connecté ✓"
```

#### Option 3 — Token manuel

```
Tool :
  → Retourne : "Collez votre token d'accès Matrix :"

Utilisateur : "syt_xxx_yyy"

Tool :
  → Appelle POST /admin/setup/login-token
  → Le backend vérifie le token via GET /_matrix/client/v3/account/whoami
  → Stocke le token

"Connecté ✓ Compte : @prenom.nom:agent.tchap.gouv.fr"
```

### Flow de connexion — Mode groupe

Un utilisateur membre d'un groupe ayant les droits de gestion du tool :

```
Utilisateur : "Configure Tchap pour le groupe Projet-Alpha"

Tool :
  → Vérifie que l'utilisateur est membre du groupe "Projet-Alpha" via l'API OpenWebUI
  → Vérifie que le groupe a les droits de gestion du tool (via les permissions du groupe)
  → Même flow de connexion (SSO/password/token) mais avec owner_type='group', owner_id=group_uuid
  → Le compte configuré est un compte de service partagé

"Compte configuré pour le groupe Projet-Alpha.
 Tous les membres du groupe pourront accéder aux salons suivis."
```

### Sélection des salons (après connexion)

```
Tool :
  → Appelle GET /admin/discover-rooms avec le owner_type/owner_id
  → Liste les salons rejoints par le compte

"## Vos salons Tchap

 1. ✓ Projet Alpha (suivi)
 2.   Discussion générale
 3.   Support technique
 4.   Veille réglementaire

Quels salons voulez-vous suivre ? (numéros séparés par des virgules)"

Utilisateur : "2, 4"

Tool :
  → Appelle POST /admin/follow-room pour chaque salon sélectionné

"✓ 2 salons ajoutés :
 - Discussion générale
 - Veille réglementaire

Vous pouvez maintenant utiliser **tchap_analyze** pour analyser ces salons."
```

---

## Tool OpenWebUI — refonte

### Fusionner tchap_reader et tchap_admin en un seul tool avec 4 méthodes

```python
class Tools:
    class Valves(BaseModel):
        base_url: str = Field(default="http://host.docker.internal:8087")
        timeout: int = Field(default=120)

    # 1. Setup interactif
    async def tchap_setup(
        self,
        action: str = "start",
        value: str = "",
        __user__: dict = None,
        __event_emitter__ = None,
    ) -> str:
        """
        Configurer l'accès Tchap de manière interactive.
        Actions : "start", "sso", "password", "token", "select-rooms", "follow", "unfollow"
        """

    # 2. Lister les salons accessibles
    async def tchap_rooms(
        self,
        __user__: dict = None,
        __event_emitter__ = None,
    ) -> str:
        """Liste tous les salons accessibles (personnels + groupes + globaux)."""

    # 3. Analyser un salon
    async def tchap_analyze(
        self,
        room_id: str,
        question: str = "",
        since_hours: int = 168,
        __user__: dict = None,
        __event_emitter__ = None,
    ) -> str:
        """Analyse complète d'un salon Tchap."""

    # 4. Admin plateforme
    async def tchap_admin(
        self,
        action: str,
        target: str = "",
        __user__: dict = None,
        __event_emitter__ = None,
    ) -> str:
        """
        Administration (admin only) :
        - "status" : état global
        - "set-global" room_id : rendre un salon global
        - "revoke-user" user_id : révoquer l'accès d'un utilisateur
        - "list-all" : tous les accès configurés
        """
```

---

## Endpoints backend à ajouter/modifier

```
# Setup interactif
POST /setup/start              → {user, owner_type, owner_id}
POST /setup/sso-start          → {user, owner_type, owner_id} → {url, state}
GET  /setup/sso-callback       → ?loginToken=xxx&state=yyy (callback Matrix)
POST /setup/sso-complete       → {state} → {ok, user_id}
POST /setup/login-password     → {user, email, password, owner_type, owner_id}
POST /setup/login-token        → {user, token, owner_type, owner_id}

# Rooms multi-tenant
GET  /rooms?user_id=xxx        → salons accessibles par cet utilisateur (perso + groupes + global)
POST /follow-room              → {owner_type, owner_id, room_id, user}
POST /unfollow-room             → {owner_type, owner_id, room_id, user}
GET  /discover-rooms            → {owner_type, owner_id} → salons du compte Matrix

# Sync et analyse (ajouter filtre owner)
POST /sync                     → {room_id, owner_type, owner_id}
POST /summary                  → {room_id, owner_type, owner_id, since_hours}

# Admin
GET  /admin/all-access         → tous les accès configurés (admin only)
POST /admin/set-global         → {room_id} (admin only)
POST /admin/revoke             → {owner_type, owner_id} (admin only)
```

---

## Vérification des droits

Le backend doit vérifier les droits à chaque requête :

```python
async def check_access(user: dict, owner_type: str, owner_id: str) -> bool:
    """Vérifie que l'utilisateur a accès à ce owner."""
    if user["role"] == "admin":
        return True
    if owner_type == "user" and owner_id == user["id"]:
        return True
    if owner_type == "global":
        return True  # accès en lecture pour tous
    if owner_type == "group":
        # Vérifier l'appartenance au groupe via l'API OpenWebUI
        groups = await get_user_groups(user["token"])
        return owner_id in [g["id"] for g in groups]
    return False

async def check_can_manage(user: dict, owner_type: str, owner_id: str) -> bool:
    """Vérifie que l'utilisateur peut configurer ce owner."""
    if user["role"] == "admin":
        return True
    if owner_type == "user" and owner_id == user["id"]:
        return True
    if owner_type == "group":
        # Vérifier que l'utilisateur a les droits de gestion dans ce groupe
        groups = await get_user_groups(user["token"])
        group = next((g for g in groups if g["id"] == owner_id), None)
        if group and user["id"] in group.get("admin_ids", []):
            return True
    return False
```

---

## Sécurité

- Les tokens Matrix sont stockés en DB SQLite. En v1 : en clair. En v2 : chiffrés avec une clé dérivée du `WEBUI_SECRET_KEY`.
- Les mots de passe Tchap ne sont JAMAIS stockés — seul le token résultant du login est conservé.
- Avertir l'utilisateur quand le mot de passe transite par le chat (option 2).
- Les tokens sont révocables côté Tchap (`POST /_matrix/client/v3/logout`).
- L'accès aux messages est filtré par owner_type/owner_id à chaque requête.
- Un utilisateur ne peut jamais voir les messages d'un salon auquel il n'a pas accès.
- L'admin OpenWebUI peut révoquer n'importe quel accès.

---

## Contraintes de production

- Ne PAS modifier la structure existante de `browser-skill-owui` ou des pipelines.
- Le tool doit rester un seul fichier Python self-contained pour OpenWebUI.
- Le backend doit être rétro-compatible (les endpoints existants continuent de fonctionner).
- Mettre à jour le manifest.yaml pour remplacer les 2 tools tchap par un seul tool unifié.
- Le flow SSO nécessite un endpoint callback accessible — prévoir un fallback si pas d'ingress.
- Tester avec le homeserver Tchap MI réel quand possible, sinon avec un mock Synapse.

---

## Fichiers à générer

```
tchap-reader/
├── app/
│   ├── models.py              # Modèles multi-tenant
│   ├── database.py            # Tables multi-tenant + migrations
│   ├── auth.py                # NEW — vérification des droits, appel API OpenWebUI
│   ├── setup_service.py       # NEW — flow de setup interactif (SSO, password, token)
│   ├── api.py                 # Refonte avec endpoints multi-tenant
│   ├── matrix_client.py       # Adapter pour multi-compte
│   ├── sync_service.py        # Adapter pour owner_type/owner_id
│   ├── summary_service.py     # Adapter pour owner_type/owner_id
│   └── openwebui_tchap_tool.py # Tool unifié (4 méthodes)
├── tests/
│   ├── test_auth.py           # Tests droits
│   ├── test_setup_service.py  # Tests flow setup
│   └── test_api_multitenant.py # Tests multi-tenant
└── prompts/
    └── tchap_multiuser_prompt.md  # Ce fichier
```
