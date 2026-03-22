#!/usr/bin/env python3
"""
Script interactif pour tester les credentials Tchap et vérifier
que les méthodes de login fonctionnent correctement.

Usage:
    python3 scripts/test_tchap_login.py
"""

import asyncio
import json
import os
import sys
import webbrowser
from getpass import getpass
from urllib.parse import quote, urlparse

import httpx

HOMESERVER = os.environ.get("TCHAP_HOMESERVER_URL", "https://matrix.agent.tchap.gouv.fr")
BACKEND_URL = os.environ.get("TCHAP_BACKEND_URL", "http://localhost:8087")
SAVED_EMAIL = os.environ.get("TCHAP_EMAIL", "")

ENV_FILE = os.path.expanduser("~/.tchap_test_env")

# ─── Couleurs terminal ───────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}ℹ{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def load_saved_env() -> dict:
    """Charger les variables sauvegardées."""
    saved = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    saved[k] = v
    return saved


def save_env(**kwargs: str) -> None:
    """Sauvegarder des variables pour les prochaines exécutions."""
    saved = load_saved_env()
    saved.update({k: v for k, v in kwargs.items() if v})
    with open(ENV_FILE, "w") as f:
        f.write("# Tchap test script — variables persistées\n")
        for k, v in saved.items():
            f.write(f"{k}={v}\n")


def get_saved(key: str, default: str = "") -> str:
    """Récupérer une variable sauvegardée ou env."""
    return os.environ.get(key, load_saved_env().get(key, default))


def header(msg: str) -> None:
    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}  {msg}{RESET}")
    print(f"{BOLD}{'─' * 60}{RESET}\n")


# ─── Tests ────────────────────────────────────────────────────

async def test_homeserver(hs: str) -> bool:
    """Test 1 : Vérifier que le homeserver est accessible."""
    header("Test 1 — Connexion au homeserver")
    info(f"URL : {hs}")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{hs}/_matrix/client/versions")
            resp.raise_for_status()
            versions = resp.json().get("versions", [])
            ok(f"Homeserver accessible — versions : {', '.join(versions[-3:])}")
            return True
    except Exception as exc:
        fail(f"Impossible de joindre le homeserver : {exc}")
        return False


async def test_login_flows(hs: str) -> dict:
    """Test 2 : Lister les méthodes de login supportées."""
    header("Test 2 — Méthodes de login supportées")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{hs}/_matrix/client/v3/login")
            resp.raise_for_status()
            flows = resp.json().get("flows", [])
    except Exception as exc:
        fail(f"Erreur : {exc}")
        return {}

    supported = {}
    for flow in flows:
        flow_type = flow.get("type", "")
        supported[flow_type] = True
        status = GREEN + "✓" + RESET
        print(f"  {status} {flow_type}")

    print()
    if "m.login.password" in supported:
        ok("Login par mot de passe : supporté")
    else:
        warn("Login par mot de passe : NON supporté")

    if "m.login.sso" in supported:
        ok("Login SSO : supporté")
    else:
        warn("Login SSO : NON supporté")

    if "m.login.token" in supported:
        ok("Login par token : supporté")
    else:
        warn("Login par token : NON supporté")

    return supported


def email_to_mxid(email: str, hs: str) -> str:
    """Convertir un email en MXID Tchap."""
    parsed = urlparse(hs)
    host = parsed.hostname or ""
    server = host.removeprefix("matrix.")
    local_part = email.replace("@", "-")
    return f"@{local_part}:{server}"


async def discover_homeserver(email: str, default_hs: str) -> str:
    """Découvrir le bon homeserver Tchap pour un email."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{default_hs}/_matrix/identity/api/v1/info",
                params={"medium": "email", "address": email},
            )
            if resp.status_code == 200:
                hs_name = resp.json().get("hs")
                if hs_name:
                    return f"https://matrix.{hs_name}"
    except Exception:
        pass
    return default_hs


async def test_login_password(hs: str) -> dict | None:
    """Test 3 : Login par email + mot de passe."""
    header("Test 3 — Login par email + mot de passe")

    saved_email = get_saved("TCHAP_EMAIL")
    if saved_email:
        email = input(f"  Email Tchap [{saved_email}] : ").strip() or saved_email
    else:
        email = input(f"  Email Tchap : ").strip()
    if not email:
        warn("Email vide, test ignoré")
        return None

    # Sauvegarder l'email pour la prochaine fois
    save_env(TCHAP_EMAIL=email)

    password = getpass(f"  Mot de passe : ")
    if not password:
        warn("Mot de passe vide, test ignoré")
        return None

    # Auto-découverte du homeserver
    info(f"Recherche du homeserver pour {email}...")
    discovered_hs = await discover_homeserver(email, hs)
    if discovered_hs != hs:
        ok(f"Homeserver découvert : {discovered_hs}")
        hs = discovered_hs
    else:
        info(f"Homeserver par défaut : {hs}")

    mxid = email_to_mxid(email, hs)
    info(f"MXID dérivé : {mxid}")

    # Essai 1 : m.id.user (format Tchap)
    print(f"\n  Essai avec {CYAN}m.id.user{RESET} :")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{hs}/_matrix/client/v3/login",
                json={
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": mxid},
                    "password": password,
                },
            )
    except Exception as exc:
        fail(f"Erreur réseau : {exc}")
        return None

    if resp.status_code == 200:
        data = resp.json()
        data["_hs"] = hs
        ok(f"Connecté ! user_id = {data.get('user_id')}")
        ok(f"access_token = {data.get('access_token', '')}")
        ok(f"device_id = {data.get('device_id')}")
        ok(f"homeserver = {hs}")
        return data

    error = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    fail(f"HTTP {resp.status_code} : {error}")

    # Essai 2 : m.id.thirdparty (standard Matrix, souvent pas supporté par Tchap)
    print(f"\n  Essai avec {CYAN}m.id.thirdparty{RESET} :")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{hs}/_matrix/client/v3/login",
                json={
                    "type": "m.login.password",
                    "identifier": {
                        "type": "m.id.thirdparty",
                        "medium": "email",
                        "address": email,
                    },
                    "password": password,
                },
            )
    except Exception as exc:
        fail(f"Erreur réseau : {exc}")
        return None

    if resp.status_code == 200:
        data = resp.json()
        data["_hs"] = hs
        ok(f"Connecté ! user_id = {data.get('user_id')}")
        ok(f"access_token = {data.get('access_token', '')}")
        warn("Note : m.id.thirdparty fonctionne — le code setup_service.py devrait aussi utiliser ce format")
        return data

    error = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    fail(f"HTTP {resp.status_code} : {error}")

    # Essai 3 : email brut dans user (certains homeservers)
    print(f"\n  Essai avec {CYAN}email brut dans user{RESET} :")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{hs}/_matrix/client/v3/login",
                json={
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": email},
                    "password": password,
                },
            )
    except Exception as exc:
        fail(f"Erreur réseau : {exc}")
        return None

    if resp.status_code == 200:
        data = resp.json()
        data["_hs"] = hs
        ok(f"Connecté ! user_id = {data.get('user_id')}")
        ok(f"access_token = {data.get('access_token', '')}")
        warn("Note : l'email brut fonctionne dans m.id.user")
        return data

    error = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    fail(f"HTTP {resp.status_code} : {error}")

    fail("Aucun format de login par mot de passe n'a fonctionné")
    return None


async def test_login_token(hs: str) -> dict | None:
    """Test 4 : Login par access token existant."""
    header("Test 4 — Vérification d'un access token")

    saved_token = get_saved("TCHAP_ACCESS_TOKEN")
    prompt = f"  Access token [{saved_token[:20]}...] : " if saved_token else "  Access token (ou Entrée pour passer) : "
    token = input(prompt).strip() or saved_token
    if not token:
        warn("Token vide, test ignoré")
        return None

    # Tester sur le HS par défaut puis sur les autres si 401
    homeservers_to_try = [hs]
    for other_hs in [
        "https://matrix.agent.tchap.gouv.fr",
        "https://matrix.agent.interieur.tchap.gouv.fr",
        "https://matrix.agent.finances.tchap.gouv.fr",
        "https://matrix.agent.social.tchap.gouv.fr",
        "https://matrix.agent.education.tchap.gouv.fr",
        "https://matrix.agent.externe.tchap.gouv.fr",
    ]:
        if other_hs not in homeservers_to_try:
            homeservers_to_try.append(other_hs)

    for try_hs in homeservers_to_try:
        info(f"Appel whoami sur {try_hs}...")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{try_hs}/_matrix/client/v3/account/whoami",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:
            fail(f"Erreur réseau : {exc}")
            continue

        if resp.status_code == 200:
            data = resp.json()
            ok(f"Token valide ! user_id = {data.get('user_id')}")
            ok(f"device_id = {data.get('device_id', 'N/A')}")
            ok(f"homeserver = {try_hs}")
            return {**data, "access_token": token, "_hs": try_hs}

        if resp.status_code == 401:
            fail(f"{try_hs} → 401 (pas le bon homeserver)")
        else:
            fail(f"{try_hs} → HTTP {resp.status_code}")

    fail("Token invalide ou expiré sur tous les homeservers connus")
    return None


async def test_sso_flow(hs: str) -> dict | None:
    """Test 5 : Flow SSO (ouvre le navigateur)."""
    header("Test 5 — Flow SSO")

    answer = input("  Tester le SSO ? (o/n) : ").strip().lower()
    if answer != "o":
        warn("Test SSO ignoré")
        return None

    # Vérifier que le backend est accessible
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{BACKEND_URL}/healthz")
            resp.raise_for_status()
            ok(f"Backend accessible sur {BACKEND_URL}")
    except Exception:
        fail(f"Backend non accessible sur {BACKEND_URL}")
        warn("Le SSO nécessite le backend pour le callback")
        return None

    # Démarrer le flow SSO via le backend
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BACKEND_URL}/setup/sso-start",
                json={"owner_type": "user", "owner_id": "test-sso"},
                headers={"X-User-Id": "test-sso", "X-User-Role": "user"},
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        fail(f"Erreur SSO start : {exc}")
        return None

    if not result.get("ok"):
        fail(f"SSO non disponible : {result.get('message')}")
        return None

    sso_url = result["url"]
    state = result["state"]

    ok(f"URL SSO générée (state={state[:8]}...)")
    info(f"URL : {sso_url[:80]}...")

    print(f"\n  {YELLOW}Ouverture du navigateur...{RESET}")
    print(f"  Connectez-vous, puis revenez ici.\n")

    webbrowser.open(sso_url)

    input(f"  {BOLD}Appuyez sur Entrée une fois connecté...{RESET}")

    # Vérifier le callback
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BACKEND_URL}/setup/sso-complete",
                json={"state": state},
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        fail(f"Erreur SSO complete : {exc}")
        return None

    if result.get("ok"):
        ok(f"SSO réussi ! user_id = {result.get('user_id')}")
        return result
    else:
        fail(f"SSO incomplet : {result.get('message')}")
        warn("Le callback n'a peut-être pas été reçu. Vérifiez les logs du backend.")
        return None


async def _fetch_room_name(hs: str, token: str, room_id: str, client: httpx.AsyncClient) -> str:
    """Fetch a single room name."""
    try:
        from urllib.parse import quote as url_quote
        resp = await client.get(
            f"{hs}/_matrix/client/v3/rooms/{url_quote(room_id, safe='')}/state/m.room.name",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return resp.json().get("name", room_id)
    except Exception:
        pass
    return ""


async def test_joined_rooms(hs: str, token: str) -> None:
    """Test 6 : Lister les salons rejoints avec le token."""
    header("Test 6 — Salons rejoints")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{hs}/_matrix/client/v3/joined_rooms",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            rooms = resp.json().get("joined_rooms", [])
    except Exception as exc:
        fail(f"Erreur : {exc}")
        return

    ok(f"{len(rooms)} salon(s) rejoint(s)")

    if not rooms:
        warn("Aucun salon. Invitez le compte dans un salon via Tchap.")
        return

    # Récupérer les noms en parallèle (batch de 5)
    info("Chargement des noms de salons...")
    room_details = []
    async with httpx.AsyncClient(timeout=10) as client:
        batch_size = 5
        for i in range(0, len(rooms), batch_size):
            batch = rooms[i:i + batch_size]
            tasks = [_fetch_room_name(hs, token, rid, client) for rid in batch]
            names = await asyncio.gather(*tasks)
            for j, (room_id, name) in enumerate(zip(batch, names)):
                idx = i + j + 1
                display_name = name or room_id.split(":")[0]
                room_details.append({"idx": idx, "name": display_name, "room_id": room_id})
                print(f"    {CYAN}{idx:3}.{RESET} {display_name}")
                print(f"         {room_id}")

    # Recherche interactive
    print(f"\n  {BOLD}Recherche de salons{RESET} (tapez un mot pour filtrer, ou Entrée pour tout voir)")

    while True:
        query = input(f"\n  🔍 Recherche (ou 'q' pour quitter) : ").strip().lower()
        if query == "q" or query == "":
            break

        matches = [r for r in room_details if query in r["name"].lower() or query in r["room_id"].lower()]
        if not matches:
            warn(f"Aucun salon trouvé pour '{query}'")
            continue

        print()
        for r in matches:
            print(f"    {CYAN}{r['idx']:3}.{RESET} {r['name']}")
            print(f"         {r['room_id']}")

        # Proposer de suivre
        answer = input(f"\n  Suivre ces salons ? (numéros séparés par virgule, ou Entrée pour chercher à nouveau) : ").strip()
        if answer:
            await _follow_rooms(answer, room_details)

    # Proposition finale si pas de recherche
    if room_details:
        answer = input(f"\n  Suivre des salons ? (numéros séparés par virgule, ou Entrée pour passer) : ").strip()
        if answer:
            await _follow_rooms(answer, room_details)


async def _follow_rooms(answer: str, room_details: list) -> None:
    """Follow selected rooms via the backend."""
    selected = []
    for part in answer.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            match = next((r for r in room_details if r["idx"] == idx), None)
            if match:
                selected.append(match)

    if not selected:
        warn("Aucun numéro valide")
        return

    info(f"Suivi de {len(selected)} salon(s) via le backend...")
    for room in selected:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/follow-room",
                    json={
                        "room_id": room["room_id"],
                        "name": room["name"],
                        "owner_type": "user",
                        "owner_id": "test-script",
                    },
                    headers={"X-User-Id": "test-script", "X-User-Role": "user"},
                )
                if resp.status_code == 200 and resp.json().get("ok"):
                    ok(f"Suivi : {room['name']}")
                else:
                    fail(f"Échec : {room['name']} — {resp.text[:100]}")
        except Exception as exc:
            fail(f"Erreur : {room['name']} — {exc}")


async def test_backend_integration(token: str, user_id: str, actual_hs: str = "") -> None:
    """Test 7 : Tester l'intégration avec le backend tchap-reader."""
    header("Test 7 — Intégration backend tchap-reader")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{BACKEND_URL}/healthz")
            resp.raise_for_status()
            ok(f"Backend accessible sur {BACKEND_URL}")
    except Exception:
        fail(f"Backend non accessible sur {BACKEND_URL}")
        return

    # Tester le login-token via le backend
    info(f"Test login-token via le backend (token={token[:20]}...)...")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{BACKEND_URL}/setup/login-token",
                json={
                    "token": token,
                    "owner_type": "user",
                    "owner_id": "test-script",
                },
                headers={
                    "X-User-Id": "test-script",
                    "X-User-Role": "user",
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        fail(f"Erreur : {exc}")
        return

    if result.get("ok"):
        ok(f"Backend login-token OK — user_id = {result.get('user_id')}")
    else:
        fail(f"Backend login-token échoué : {result.get('message')}")
        return

    # Vérifier les salons suivis
    info("Vérification des salons suivis...")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BACKEND_URL}/rooms",
                params={"user_id": "test-script"},
                headers={"X-User-Id": "test-script", "X-User-Role": "user"},
            )
            resp.raise_for_status()
            rooms = resp.json()
    except Exception as exc:
        fail(f"Erreur : {exc}")
        return

    if rooms:
        ok(f"{len(rooms)} salon(s) suivis dans le backend")
        for r in rooms:
            print(f"    {GREEN}✓{RESET} {r.get('name', '?')} — {r.get('room_id', '?')}")
    else:
        info("Aucun salon suivi. Utilisez la recherche au test 6 pour en ajouter.")


async def main():
    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Test des credentials Tchap — Script interactif{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")

    saved_hs = get_saved("TCHAP_HOMESERVER_URL", HOMESERVER)
    hs = input(f"\n  Homeserver [{saved_hs}] : ").strip() or saved_hs

    # Test 1 : Homeserver
    if not await test_homeserver(hs):
        sys.exit(1)

    # Test 2 : Login flows
    flows = await test_login_flows(hs)
    if not flows:
        sys.exit(1)

    # Test 3, 4, 5 : Méthodes de login
    valid_token = None
    valid_user_id = None
    actual_hs = hs  # Le homeserver réel (peut changer après discovery)

    # Mot de passe
    if "m.login.password" in flows:
        result = await test_login_password(hs)
        if result:
            valid_token = result.get("access_token")
            valid_user_id = result.get("user_id")
            actual_hs = result.get("_hs", hs)

    # Token existant
    if not valid_token:
        result = await test_login_token(hs)
        if result:
            valid_token = result.get("access_token")
            valid_user_id = result.get("user_id")
            actual_hs = result.get("_hs", hs)

    # SSO
    if not valid_token and "m.login.sso" in flows:
        result = await test_sso_flow(hs)
        if result:
            valid_user_id = result.get("user_id")

    # Test 6 : Salons (utiliser le homeserver découvert)
    if valid_token:
        await test_joined_rooms(actual_hs, valid_token)

        # Test 7 : Backend
        await test_backend_integration(valid_token, valid_user_id or "", actual_hs)

    # Résumé
    header("Résumé")
    if valid_token:
        ok(f"Connexion réussie : {valid_user_id}")
        ok(f"Homeserver : {actual_hs}")
        ok(f"Token : {valid_token}")

        # Persister pour les prochaines exécutions
        save_env(
            TCHAP_HOMESERVER_URL=actual_hs,
            TCHAP_ACCESS_TOKEN=valid_token,
            TCHAP_USER_ID=valid_user_id or "",
        )
        info(f"Sauvegardé dans {ENV_FILE}")

        print(f"\n  Vous pouvez utiliser ce token dans OpenWebUI :")
        print(f"  {CYAN}\"Connecte-moi à Tchap avec le token {valid_token}\"{RESET}")
    else:
        fail("Aucune méthode de connexion n'a réussi")
        print(f"\n  Vérifiez :")
        print(f"  - Votre email et mot de passe Tchap")
        print(f"  - Que votre compte est bien sur {hs}")
        print(f"  - Que le homeserver est le bon pour votre domaine")

    print()


if __name__ == "__main__":
    asyncio.run(main())
