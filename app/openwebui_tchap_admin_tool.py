"""
title: Tchap Admin - Configuration des salons Matrix
description: Configurer le compte bot Tchap, découvrir les salons disponibles, choisir lesquels suivre. Outil d'administration. Compatible avec le tool unifié tchap_reader v0.2.
author: tchap-reader
version: 0.2.0
"""

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        """Configuration dans le panneau admin OpenWebUI."""
        base_url: str = Field(
            default="http://host.docker.internal:8087",
            description="URL du service tchap-reader",
        )
        timeout: int = Field(
            default=30,
            description="Timeout en secondes",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _user_headers(self, user: dict | None) -> dict[str, str]:
        if not user:
            return {}
        return {
            "X-User-Id": user.get("id", ""),
            "X-User-Email": user.get("email", ""),
            "X-User-Role": user.get("role", "user"),
            "X-User-Token": user.get("token", ""),
        }

    async def tchap_status(
        self,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Affiche l'état de la configuration Tchap : compte, salons suivis, nombre de messages.
        Utile pour vérifier si le bot est correctement configuré.

        :return: État complet de la configuration.
        """
        import httpx

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Vérification de la configuration...", "done": False}})

        headers = self._user_headers(__user__)

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(f"{self.valves.base_url}/admin/status", headers=headers)
                resp.raise_for_status()
                status = resp.json()
        except Exception as exc:
            return f"# Erreur\n\nImpossible de contacter le service tchap-reader : {exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

        if not status.get("configured"):
            return (
                "# Tchap — Non configuré\n\n"
                "Le compte bot Tchap n'est pas encore configuré.\n\n"
                "Pour le configurer, utilisez **tchap_configure** avec :\n"
                "- L'URL du homeserver (ex: `https://matrix.agent.tchap.gouv.fr`)\n"
                "- L'identifiant Matrix du bot (ex: `@bot:agent.tchap.gouv.fr`)\n"
                "- Le token d'accès du bot\n\n"
                "Ensuite, utilisez **tchap_discover_and_follow** pour choisir les salons à suivre."
            )

        lines = [
            "# Tchap — Configuration\n",
            f"- **Homeserver** : `{status.get('homeserver_url', 'N/A')}`",
            f"- **Compte bot** : `{status.get('user_id', 'N/A')}`",
            f"- **Messages stockés** : {status.get('total_messages', 0)}",
            f"- **Comptes configurés** : {status.get('accounts', 0)}",
            f"- **Salons suivis** : {len(status.get('allowed_rooms', []))}",
        ]

        if status.get("allowed_rooms"):
            lines.append("\n## Salons suivis\n")
            for rid in status["allowed_rooms"]:
                lines.append(f"- `{rid}`")

        if not status.get("allowed_rooms"):
            lines.append(
                "\n> Aucun salon suivi. Utilisez **tchap_discover_and_follow** "
                "pour découvrir et sélectionner les salons."
            )

        return "\n".join(lines)

    async def tchap_configure(
        self,
        homeserver_url: str,
        user_id: str,
        access_token: str,
        device_id: str = "OWUI_BOT",
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Configure le compte bot Tchap. Teste la connexion puis sauvegarde.
        Les credentials sont stockés localement dans la base du service, jamais exposés.

        :param homeserver_url: URL du homeserver Matrix (ex: https://matrix.agent.tchap.gouv.fr).
        :param user_id: Identifiant Matrix du bot (ex: @monbot:agent.tchap.gouv.fr).
        :param access_token: Token d'accès du compte bot.
        :param device_id: Device ID optionnel (défaut: OWUI_BOT).
        :return: Résultat de la configuration.
        """
        import httpx

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Test de la connexion au homeserver...", "done": False}})

        headers = self._user_headers(__user__)

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.post(
                    f"{self.valves.base_url}/admin/configure",
                    json={
                        "homeserver_url": homeserver_url,
                        "user_id": user_id,
                        "access_token": access_token,
                        "device_id": device_id,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:
            return f"# Erreur de configuration\n\n{exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Configuration terminée", "done": True}})

        if not result.get("ok"):
            return f"# Configuration échouée\n\n{result.get('message', 'Erreur inconnue')}"

        rooms = result.get("joined_rooms", [])
        lines = [
            "# Tchap — Compte configuré\n",
            f"- **Compte** : `{result.get('user_id')}`",
            f"- **Salons rejoints** : {len(rooms)}",
            "",
            "## Prochaine étape",
            "",
            "Utilisez **tchap_discover_and_follow** pour voir les salons disponibles "
            "et choisir lesquels suivre.",
        ]

        return "\n".join(lines)

    async def tchap_discover_and_follow(
        self,
        action: str = "list",
        room_id: str = "",
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Découvrir les salons disponibles et choisir lesquels suivre.

        Actions :
        - "list" : affiche tous les salons rejoints par le bot
        - "follow" : ajoute un salon à la liste de suivi (fournir room_id)
        - "unfollow" : retire un salon de la liste de suivi (fournir room_id)

        :param action: "list", "follow" ou "unfollow".
        :param room_id: ID du salon pour follow/unfollow.
        :return: Liste des salons ou confirmation de l'action.
        """
        import httpx

        headers = self._user_headers(__user__)

        if action == "list":
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Découverte des salons...", "done": False}})

            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.get(
                        f"{self.valves.base_url}/admin/discover-rooms",
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            if not result.get("ok"):
                return f"# Erreur\n\n{result.get('message', 'Non configuré')}"

            rooms = result.get("rooms", [])
            if not rooms:
                return "# Aucun salon\n\nLe bot n'a rejoint aucun salon. Invitez-le dans un salon via Tchap."

            lines = ["# Salons disponibles\n"]
            followed = [r for r in rooms if r.get("followed")]
            not_followed = [r for r in rooms if not r.get("followed")]

            if followed:
                lines.append("## Salons suivis\n")
                for r in followed:
                    lines.append(f"- **{r['name']}** — `{r['room_id']}`")

            if not_followed:
                lines.append("\n## Salons non suivis\n")
                for r in not_followed:
                    lines.append(f"- {r['name']} — `{r['room_id']}`")

            lines.append(
                "\n---\n"
                "Pour suivre un salon : `tchap_discover_and_follow(action=\"follow\", room_id=\"!xxx:server\")`\n"
                "Pour arrêter de suivre : `tchap_discover_and_follow(action=\"unfollow\", room_id=\"!xxx:server\")`"
            )

            return "\n".join(lines)

        elif action in ("follow", "unfollow"):
            if not room_id:
                return "Erreur : fournissez un room_id. Utilisez d'abord `action=\"list\"` pour voir les IDs."

            endpoint = "follow-room" if action == "follow" else "unfollow-room"
            verb = "Ajout" if action == "follow" else "Retrait"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": f"{verb} du salon...", "done": False}})

            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/admin/{endpoint}",
                        json={"room_id": room_id},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            if result.get("ok"):
                followed = result.get("allowed_rooms", [])
                return (
                    f"# {verb} effectué\n\n"
                    f"{result.get('message', '')}\n\n"
                    f"**Salons suivis ({len(followed)})** : {', '.join(f'`{r}`' for r in followed) or 'aucun'}"
                )
            else:
                return f"# Erreur\n\n{result.get('message', 'Erreur inconnue')}"

        else:
            return f"Action inconnue : `{action}`. Utilisez `list`, `follow` ou `unfollow`."
