"""Interactive setup flow — SSO, password, and token login for Matrix accounts."""

from __future__ import annotations

import logging
import uuid

import httpx

from app.config import settings
from app.database import Database

logger = logging.getLogger(__name__)


class SetupService:
    """Manages Matrix account setup flows (SSO, password, token)."""

    def __init__(self, db: Database):
        self._db = db

    @staticmethod
    async def discover_homeserver(email: str) -> str | None:
        """Discover the correct Tchap homeserver for an email address.

        Uses the Tchap identity API to find the right HS for the user's domain.
        Returns the full homeserver URL or None if discovery fails.
        """
        # Try the identity API on the default homeserver
        default_hs = settings.TCHAP_HOMESERVER_URL.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{default_hs}/_matrix/identity/api/v1/info",
                    params={"medium": "email", "address": email},
                )
                if resp.status_code == 200:
                    hs_name = resp.json().get("hs")
                    if hs_name:
                        discovered = f"https://matrix.{hs_name}"
                        logger.info("Discovered homeserver for %s: %s", email, discovered)
                        return discovered
        except Exception as exc:
            logger.warning("Homeserver discovery failed for %s: %s", email, exc)
        return None

    async def start_sso(
        self,
        owner_type: str,
        owner_id: str,
        user_uuid: str,
        homeserver_url: str | None = None,
    ) -> dict:
        """Start the SSO login flow.

        1. Check if SSO is available on the homeserver
        2. Generate a state token
        3. Return the redirect URL
        """
        hs = (homeserver_url or settings.TCHAP_HOMESERVER_URL).rstrip("/")

        # Check login flows
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{hs}/_matrix/client/v3/login")
            resp.raise_for_status()
            flows = resp.json().get("flows", [])

        sso_available = any(f.get("type") == "m.login.sso" for f in flows)
        if not sso_available:
            return {
                "ok": False,
                "message": "SSO non disponible sur ce homeserver. Utilisez la méthode email/mot de passe ou token.",
            }

        # Generate state and store session
        state = str(uuid.uuid4())
        self._db.create_sso_session(state, owner_type, owner_id, user_uuid)

        # Build redirect URL (must URL-encode the callback)
        from urllib.parse import quote
        callback_url = f"{settings.SSO_CALLBACK_BASE_URL.rstrip('/')}/setup/sso-callback?state={state}"
        redirect_url = f"{hs}/_matrix/client/v3/login/sso/redirect?redirectUrl={quote(callback_url, safe='')}"

        return {
            "ok": True,
            "url": redirect_url,
            "state": state,
            "message": "Cliquez sur le lien pour vous connecter via SSO.",
        }

    async def handle_sso_callback(
        self,
        login_token: str,
        state: str,
    ) -> dict:
        """Handle the SSO callback from Matrix.

        1. Exchange loginToken for access_token
        2. Store the result in the SSO session
        """
        session = self._db.get_sso_session(state)
        if not session:
            return {"ok": False, "message": "Session SSO invalide ou expirée."}

        # Get homeserver for this owner (use existing account or default)
        account = self._db.get_matrix_account(session["owner_type"], session["owner_id"])
        hs = account["homeserver_url"] if account else settings.TCHAP_HOMESERVER_URL
        hs = hs.rstrip("/")

        # Exchange token
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{hs}/_matrix/client/v3/login",
                json={
                    "type": "m.login.token",
                    "token": login_token,
                },
            )
            if resp.status_code != 200:
                return {"ok": False, "message": f"Échange de token échoué : {resp.text}"}
            data = resp.json()

        access_token = data.get("access_token", "")
        matrix_user_id = data.get("user_id", "")
        device_id = data.get("device_id", "OWUI_BOT")

        # Complete SSO session
        self._db.complete_sso_session(state, access_token, matrix_user_id)

        # Save the Matrix account
        self._db.save_matrix_account(
            owner_type=session["owner_type"],
            owner_id=session["owner_id"],
            homeserver_url=hs,
            user_id=matrix_user_id,
            access_token=access_token,
            device_id=device_id,
            configured_by=session["user_uuid"],
        )

        return {"ok": True, "user_id": matrix_user_id}

    async def complete_sso(self, state: str) -> dict:
        """Check if SSO callback has been received for this state."""
        session = self._db.get_sso_session(state)
        if not session:
            return {"ok": False, "message": "Session SSO invalide ou expirée."}
        if not session["completed"]:
            return {"ok": False, "message": "En attente de la connexion SSO. Cliquez sur le lien puis revenez ici."}

        return {
            "ok": True,
            "message": f"Connecté. Compte : {session['matrix_user_id']}",
            "user_id": session["matrix_user_id"],
        }

    @staticmethod
    def _email_to_tchap_mxid(email: str, homeserver_url: str) -> str:
        """Convert an email to a Tchap Matrix ID.

        Tchap format: @prenom.nom-domain.gouv.fr:agent.tchap.gouv.fr
        The '@' in the email becomes '-', and the server part comes from the homeserver.
        """
        # Extract server from homeserver URL (e.g. agent.tchap.gouv.fr)
        from urllib.parse import urlparse
        parsed = urlparse(homeserver_url)
        host = parsed.hostname or ""
        # Remove "matrix." prefix if present
        server = host.removeprefix("matrix.")

        # Convert email: prenom.nom@domain.gouv.fr → prenom.nom-domain.gouv.fr
        local_part = email.replace("@", "-")
        return f"@{local_part}:{server}"

    async def login_password(
        self,
        email: str,
        password: str,
        owner_type: str,
        owner_id: str,
        user_uuid: str,
        homeserver_url: str | None = None,
    ) -> dict:
        """Login with email and password.

        Tchap uses m.id.user with MXID derived from email, not m.id.thirdparty.
        """
        # Auto-discover the correct homeserver for this email
        if not homeserver_url:
            discovered = await self.discover_homeserver(email)
            hs = (discovered or settings.TCHAP_HOMESERVER_URL).rstrip("/")
        else:
            hs = homeserver_url.rstrip("/")

        # Convert email to Tchap MXID
        mxid = self._email_to_tchap_mxid(email, hs)
        logger.info("Login attempt for %s on %s (MXID: %s)", email, hs, mxid)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{hs}/_matrix/client/v3/login",
                    json={
                        "type": "m.login.password",
                        "identifier": {
                            "type": "m.id.user",
                            "user": mxid,
                        },
                        "password": password,
                    },
                )
                if resp.status_code in (401, 403):
                    error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    error_msg = error_body.get("error", "Identifiants incorrects")
                    return {"ok": False, "message": f"Échec : {error_msg}", "user_id": ""}
                if resp.status_code == 400:
                    error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    error_msg = error_body.get("error", "Requête invalide")
                    logger.warning("Login 400 for %s: %s", mxid, error_msg)
                    return {"ok": False, "message": f"Erreur : {error_msg}", "user_id": ""}
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            return {"ok": False, "message": f"Erreur de connexion : {exc}", "user_id": ""}
        except Exception as exc:
            return {"ok": False, "message": f"Erreur réseau : {exc}", "user_id": ""}

        access_token = data.get("access_token", "")
        matrix_user_id = data.get("user_id", "")
        device_id = data.get("device_id", "OWUI_BOT")

        self._db.save_matrix_account(
            owner_type=owner_type,
            owner_id=owner_id,
            homeserver_url=hs,
            user_id=matrix_user_id,
            access_token=access_token,
            device_id=device_id,
            configured_by=user_uuid,
        )

        return {"ok": True, "message": f"Connecté. Compte : {matrix_user_id}", "user_id": matrix_user_id}

    KNOWN_HOMESERVERS = [
        "https://matrix.agent.tchap.gouv.fr",
        "https://matrix.agent.interieur.tchap.gouv.fr",
        "https://matrix.agent.finances.tchap.gouv.fr",
        "https://matrix.agent.social.tchap.gouv.fr",
        "https://matrix.agent.education.tchap.gouv.fr",
        "https://matrix.agent.externe.tchap.gouv.fr",
    ]

    async def login_token(
        self,
        token: str,
        owner_type: str,
        owner_id: str,
        user_uuid: str,
        homeserver_url: str | None = None,
    ) -> dict:
        """Login with a pre-existing access token.

        Tries the specified homeserver first, then all known Tchap homeservers.
        """
        # Build list of homeservers to try
        homeservers = []
        if homeserver_url:
            homeservers.append(homeserver_url.rstrip("/"))
        default = settings.TCHAP_HOMESERVER_URL.rstrip("/")
        if default not in homeservers:
            homeservers.append(default)
        for known in self.KNOWN_HOMESERVERS:
            if known not in homeservers:
                homeservers.append(known)

        data = None
        hs = homeservers[0]

        for try_hs in homeservers:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"{try_hs}/_matrix/client/v3/account/whoami",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        hs = try_hs
                        logger.info("Token validated on %s: %s", try_hs, data.get("user_id"))
                        break
                    elif resp.status_code == 401:
                        logger.debug("Token rejected by %s", try_hs)
                        continue
                    else:
                        continue
            except Exception as exc:
                logger.debug("Error trying %s: %s", try_hs, exc)
                continue

        if data is None:
            return {"ok": False, "message": "Token invalide ou expiré sur tous les homeservers Tchap connus.", "user_id": ""}

        matrix_user_id = data.get("user_id", "")
        device_id = data.get("device_id", "OWUI_BOT")

        self._db.save_matrix_account(
            owner_type=owner_type,
            owner_id=owner_id,
            homeserver_url=hs,
            user_id=matrix_user_id,
            access_token=token,
            device_id=device_id,
            configured_by=user_uuid,
        )

        return {"ok": True, "message": f"Connecté. Compte : {matrix_user_id}", "user_id": matrix_user_id}
