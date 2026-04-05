# Mode d'emploi — Paramétrage Tchap Reader dans Open WebUI

## 1. Vérifier que les tools sont installés

1. Ouvrir Open WebUI : **http://localhost:3000**
2. Se connecter avec un compte **admin**
3. Aller dans **Workspace > Tools** (icône clé dans le menu gauche)
4. Vous devez voir 2 tools Tchap :

   | Tool | Méthodes | Usage |
   |------|----------|-------|
   | **Tchap Reader** | `tchap_setup`, `tchap_rooms`, `tchap_analyze`, `tchap_admin` | Tool principal (v0.2) |
   | **Tchap Admin** | `tchap_status`, `tchap_configure`, `tchap_discover_and_follow` | Rétro-compatible |

> Si les tools n'apparaissent pas, relancer :
> ```bash
> cd grafrag-experimentation
> python3 scripts/register_all_openwebui_tools.py --mode docker
> ```

---

## 2. Configurer les Valves

Les Valves sont les paramètres de connexion du tool vers le backend.

1. Dans **Workspace > Tools**, cliquer sur **Tchap Reader**
2. Cliquer sur l'icône **engrenage** (Valves) en haut à droite
3. Vérifier / modifier :

   | Valve | Valeur | Description |
   |-------|--------|-------------|
   | `base_url` | `http://tchapreader:8087` | URL du backend (réseau Docker interne) |
   | `timeout` | `120` | Timeout en secondes |
   | `default_since_hours` | `168` | Fenêtre par défaut (168h = 7 jours) |

4. **Sauvegarder**
5. Répéter pour **Tchap Admin** (même `base_url`)

> **Important** : l'URL doit être `http://tchapreader:8087` (nom du conteneur Docker), **pas** `http://localhost:8087` car les tools s'exécutent depuis le conteneur OpenWebUI.

---

## 3. Activer les tools dans un modèle

Les tools ne sont pas actifs par défaut — il faut les associer à un modèle ou les activer dans une conversation.

### Option A — Activer par conversation (ponctuel)

1. Ouvrir une nouvelle conversation
2. Cliquer sur l'icône **+** à côté du champ de saisie
3. Dans la section **Tools**, cocher :
   - **Tchap Reader**
   - **Tchap Admin** (si besoin)
4. Les tools sont maintenant disponibles pour cette conversation

### Option B — Activer par défaut sur un modèle (recommandé)

1. Aller dans **Workspace > Models**
2. Créer ou éditer un modèle (ex: "Assistant Tchap")
3. Dans la section **Tools & Functions** :
   - Activer **Tchap Reader**
   - Activer **Tchap Admin** (optionnel)
4. Sauvegarder
5. Tous les utilisateurs de ce modèle auront accès aux tools

---

## 4. Première utilisation — Configurer son accès Tchap

### Mode A — Compte personnel (chaque utilisateur)

Dans le chat, écrire :

```
Configure mon accès Tchap
```

Le LLM appellera `tchap_setup(action="start")` et guidera l'utilisateur :

```
1. SSO / France Connect Agent → recommandé
2. Email et mot de passe → le mdp transite par le chat
3. Token d'accès → si vous avez déjà un token Matrix
```

**Option 3 (token) — la plus simple pour tester :**

1. Ouvrir Element (ou Element Web)
2. Paramètres > Aide & À propos > Token d'accès (avancé)
3. Copier le token
4. Dans le chat :
   ```
   Utilise le token syt_xxxx_yyyy pour me connecter
   ```
   → Le LLM appellera `tchap_setup(action="token", value="syt_xxxx_yyyy")`

5. Ensuite :
   ```
   Quels salons sont disponibles ?
   ```
   → Le LLM appellera `tchap_setup(action="select-rooms")`

6. Puis :
   ```
   Suis les salons 1 et 3
   ```
   → Le LLM appellera `tchap_setup(action="follow", value="!room_id:server")`

### Mode B — Compte bot global (admin seulement)

Utiliser le tool **Tchap Admin** ou écrire :

```
Configure le bot Tchap avec le token syt_bot_xxx
sur le homeserver https://matrix.agent.tchap.gouv.fr
avec le user_id @bot-mi:agent.tchap.gouv.fr
```

→ Le LLM appellera `tchap_configure(homeserver_url, user_id, access_token)`

Puis :
```
Montre les salons disponibles du bot
```
→ `tchap_discover_and_follow(action="list")`

```
Suis le salon !abc:agent.tchap.gouv.fr
```
→ `tchap_discover_and_follow(action="follow", room_id="!abc:...")`

---

## 5. Analyser un salon

Une fois configuré et des salons suivis :

```
Quels salons Tchap sont disponibles ?
```
→ `tchap_rooms()` — affiche tous les salons accessibles

```
Fais une synthèse du salon Discussion RH
```
→ `tchap_analyze(room_id="!xxx:server")` — analyse complète (6 sections)

```
Quels sont les irritants dans le salon Projet Alpha cette semaine ?
```
→ `tchap_analyze(room_id="!yyy:server", question="Quels sont les irritants ?", since_hours=168)`

```
Quelles tendances sur 30 jours dans le salon Support ?
```
→ `tchap_analyze(room_id="!zzz:server", question="Quelles tendances ?", since_hours=720)`

---

## 6. Administration (admin uniquement)

```
Quel est l'état de la plateforme Tchap ?
```
→ `tchap_admin(action="status")`

```
Liste tous les accès Tchap configurés
```
→ `tchap_admin(action="list-all")`

```
Rends le salon Annonces accessible à tous
```
→ `tchap_admin(action="set-global", target="!annonces:server")`

```
Révoque l'accès de l'utilisateur xxx
```
→ `tchap_admin(action="revoke-user", target="user-uuid")`

---

## 7. Dépannage

### Le tool ne répond pas

Vérifier que le backend tourne :
```bash
curl http://localhost:8087/healthz
```

### "Erreur de connexion"

1. Vérifier la Valve `base_url` dans OpenWebUI :
   - Doit être `http://tchapreader:8087` (pas `localhost`)
2. Vérifier que les conteneurs sont sur le même réseau Docker :
   ```bash
   docker network inspect grafrag-experimentation_default
   ```

### Le tool n'apparaît pas dans la conversation

1. Vérifier qu'il est activé (icône + > Tools > cocher)
2. Ou l'activer par défaut sur le modèle (Workspace > Models)

### "utilisateur supprimé" sur un tool

Relancer l'enregistrement :
```bash
cd grafrag-experimentation
python3 scripts/register_all_openwebui_tools.py --mode docker
```

### Token Matrix expiré

Reconfigurer :
```
Reconfigure mon accès Tchap avec le nouveau token syt_xxx
```

---

## Résumé des commandes naturelles

| Ce que vous dites | Ce qui se passe |
|-------------------|-----------------|
| "Configure mon accès Tchap" | Setup interactif (SSO/password/token) |
| "Quels salons sont disponibles ?" | Liste les salons suivis |
| "Synthèse du salon X" | Analyse complète en 6 sections |
| "Quels irritants dans X ?" | Analyse ciblée |
| "Suis le salon Y" | Ajoute un salon au suivi |
| "État de la plateforme" | Status admin |
| "Configure Tchap pour le groupe Z" | Setup multi-tenant groupe |
