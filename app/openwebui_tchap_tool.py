"""
title: Tchap - Analyse et gestion de salons Matrix/Tchap
description: Configurer l'accès Tchap, lister et analyser les salons, administrer la plateforme. Multi-utilisateur avec accès individuel, groupe et global.
author: tchapreader
version: 0.3.0
"""

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        """Configuration admin — Workspace > Tools > Tchap Reader > engrenage."""
        base_url: str = Field(
            default="http://host.docker.internal:8087",
            description="URL du service tchapreader",
        )
        timeout: int = Field(
            default=120,
            description="Timeout en secondes",
        )
        default_since_hours: int = Field(
            default=168,
            description="Fenêtre temporelle par défaut (heures). 168 = 7 jours.",
        )

    class UserValves(BaseModel):
        """Configuration par utilisateur — visible dans les paramètres du tool.

        Chaque utilisateur renseigne ses credentials ici.
        Le mot de passe est masqué et n'apparaît jamais dans le chat.
        """
        tchap_email: str = Field(
            default="",
            description="Votre email Tchap (ex: prenom.nom@interieur.gouv.fr)",
        )
        tchap_password: str = Field(
            default="",
            description="Votre mot de passe Tchap (masqué, jamais visible dans le chat)",
            json_schema_extra={"format": "password"},
        )
        tchap_token: str = Field(
            default="",
            description="OU votre token d'accès Matrix (alternative au mot de passe, ex: mct_xxx ou syt_xxx)",
            json_schema_extra={"format": "password"},
        )

    def __init__(self):
        self.valves = self.Valves()

    def _user_headers(self, user: dict | None) -> dict[str, str]:
        """Build auth headers from __user__ context."""
        if not user:
            return {}
        return {
            "X-User-Id": user.get("id", ""),
            "X-User-Email": user.get("email", ""),
            "X-User-Role": user.get("role", "user"),
            "X-User-Token": user.get("token", ""),
        }

    def _get_user_valves(self, user: dict | None) -> dict:
        """Get user-specific valve values."""
        if not user:
            return {}
        valves = user.get("valves", {})
        # OWUI passes UserValves as a Pydantic object, not a dict
        if valves and hasattr(valves, "model_dump"):
            return valves.model_dump()
        if valves and not isinstance(valves, dict):
            return {k: getattr(valves, k, "") for k in ("tchap_email", "tchap_password", "tchap_token") if hasattr(valves, k)}
        return valves or {}

    # ── 1. Connexion Tchap ───────────────────────────────────

    async def tchap_connect(
        self,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        OBLIGATOIRE : Appeler cette fonction quand l'utilisateur veut se connecter ou configurer Tchap.

        Les credentials (email/mot de passe ou token) sont lus depuis les paramètres utilisateur du tool.
        L'utilisateur doit d'abord les renseigner dans : icône engrenage du tool > paramètres utilisateur.
        Aucun mot de passe ne transite par le chat.

        :return: Résultat de la connexion — afficher tel quel.
        """
        import httpx

        owner_type = "user"
        owner_id = __user__["id"] if __user__ else ""
        headers = self._user_headers(__user__)
        uv = self._get_user_valves(__user__)

        tchap_token = uv.get("tchap_token", "")
        tchap_email = uv.get("tchap_email", "")
        tchap_password = uv.get("tchap_password", "")

        # Vérifier si déjà connecté
        account_check = None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.valves.base_url}/rooms",
                    headers=headers,
                    params={"user_id": owner_id},
                )
                if resp.status_code == 200:
                    rooms = resp.json()
                    if rooms:
                        account_check = "connected"
        except Exception:
            pass

        if account_check == "connected" and not tchap_token and not tchap_email:
            return (
                "# Déjà connecté ✓\n\n"
                "Votre compte Tchap est configuré. Vous pouvez :\n"
                "- **tchap_search_rooms** — chercher et suivre des salons\n"
                "- **tchap_rooms** — voir vos salons suivis\n"
                "- **tchap_analyze** — analyser un salon"
            )

        # Méthode 1 : Token
        if tchap_token:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Vérification du token Tchap...", "done": False}})

            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/setup/login-token",
                        json={"token": tchap_token, "owner_type": owner_type, "owner_id": owner_id},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\nImpossible de vérifier le token : {exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            if result.get("ok"):
                return (
                    f"# Connecté ✓\n\n"
                    f"Compte : `{result.get('user_id', '')}`\n\n"
                    f"Vous pouvez maintenant :\n"
                    f"- **tchap_search_rooms** — chercher et suivre des salons\n"
                    f"- **tchap_rooms** — voir vos salons suivis\n"
                    f"- **tchap_analyze** — analyser un salon"
                )
            return f"# Token invalide\n\n{result.get('message', 'Vérifiez le token dans les paramètres du tool.')}"

        # Méthode 2 : Email + mot de passe
        if tchap_email and tchap_password:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Connexion à Tchap...", "done": False}})

            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/setup/login-password",
                        json={
                            "email": tchap_email,
                            "password": tchap_password,
                            "owner_type": owner_type,
                            "owner_id": owner_id,
                        },
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\nImpossible de se connecter : {exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            if result.get("ok"):
                return (
                    f"# Connecté ✓\n\n"
                    f"Compte : `{result.get('user_id', '')}`\n\n"
                    f"Vous pouvez maintenant :\n"
                    f"- **tchap_search_rooms** — chercher et suivre des salons\n"
                    f"- **tchap_rooms** — voir vos salons suivis\n"
                    f"- **tchap_analyze** — analyser un salon\n\n"
                    f"*Conseil : vous pouvez maintenant effacer votre mot de passe des paramètres du tool.*"
                )
            return f"# Échec de connexion\n\n{result.get('message', 'Vérifiez vos identifiants dans les paramètres du tool.')}"

        # Aucun credential configuré
        return (
            "# Configuration requise\n\n"
            "Pour connecter votre compte Tchap, renseignez vos credentials dans les **paramètres du tool** :\n\n"
            "1. Cliquez sur l'icône **engrenage** à côté du nom du tool Tchap Reader\n"
            "2. Remplissez **une** des deux options :\n"
            "   - **Token d'accès** : collez votre token Matrix (mct_xxx ou syt_xxx)\n"
            "   - **Email + Mot de passe** : vos identifiants Tchap\n"
            "3. Sauvegardez\n"
            "4. Revenez ici et dites « **connecte-moi à Tchap** »\n\n"
            "*Le mot de passe est masqué et n'apparaît jamais dans le chat.*"
        )

    # ── 2. Rechercher et suivre des salons ─────────────────────

    async def tchap_search_rooms(
        self,
        query: str = "",
        follow_room_id: str = "",
        unfollow_room_id: str = "",
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        OBLIGATOIRE : Appeler cette fonction pour chercher, suivre ou retirer des salons Tchap.

        Exemples d'utilisation :
        - Chercher un salon : query="projet alpha"
        - Tout lister : query="" (sans filtre)
        - Suivre un salon : follow_room_id="!abc:server"
        - Ne plus suivre : unfollow_room_id="!abc:server"

        :param query: Mot-clé pour filtrer les salons par nom (ex: "projet", "support"). Vide = tous les salons.
        :param follow_room_id: Room ID à suivre (ex: !abc:agent.tchap.gouv.fr).
        :param unfollow_room_id: Room ID à ne plus suivre.
        :return: Liste des salons trouvés ou confirmation — afficher tel quel.
        """
        import httpx

        owner_type = "user"
        owner_id = __user__["id"] if __user__ else ""
        headers = self._user_headers(__user__)

        # Follow
        if follow_room_id:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Ajout du salon...", "done": False}})
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/follow-room",
                        json={"room_id": follow_room_id, "owner_type": owner_type, "owner_id": owner_id},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            if result.get("ok"):
                followed = result.get("followed_rooms", [])
                return (
                    f"# Salon ajouté ✓\n\n{result.get('message', '')}\n\n"
                    f"**Salons suivis ({len(followed)})** : {', '.join(f'`{r}`' for r in followed) or 'aucun'}\n\n"
                    f"Utilisez **tchap_analyze** pour analyser un salon."
                )
            return f"# Erreur\n\n{result.get('message', 'Erreur inconnue')}"

        # Unfollow
        if unfollow_room_id:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Retrait du salon...", "done": False}})
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/unfollow-room",
                        json={"room_id": unfollow_room_id, "owner_type": owner_type, "owner_id": owner_id},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            if result.get("ok"):
                followed = result.get("followed_rooms", [])
                return f"# Salon retiré ✓\n\n{result.get('message', '')}\n\n**Salons suivis ({len(followed)})** : {', '.join(f'`{r}`' for r in followed) or 'aucun'}"
            return f"# Erreur\n\n{result.get('message', 'Erreur inconnue')}"

        # Search
        if __event_emitter__:
            desc = f"Recherche de salons '{query}'..." if query else "Chargement des salons..."
            await __event_emitter__({"type": "status", "data": {"description": desc, "done": False}})

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(
                    f"{self.valves.base_url}/search-rooms",
                    params={"q": query, "owner_type": owner_type, "owner_id": owner_id},
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:
            return f"# Erreur\n\n{exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

        if not result.get("ok"):
            return f"# Erreur\n\n{result.get('message', 'Compte non configuré. Utilisez tchap_connect pour vous connecter.')}"

        rooms = result.get("rooms", [])
        total = result.get("total", 0)

        if not rooms:
            if query:
                return f"# Aucun résultat\n\nAucun salon trouvé pour « {query} » parmi vos {total} salons.\n\nEssayez un autre mot-clé ou `tchap_search_rooms()` sans filtre."
            return "# Aucun salon\n\nLe compte n'a rejoint aucun salon. Invitez-le dans un salon via Tchap."

        if query:
            lines = [f"## Résultats pour « {query} » ({len(rooms)}/{total} salons)\n"]
        else:
            lines = [f"## Vos salons Tchap ({len(rooms)} salons)\n"]

        for i, r in enumerate(rooms, 1):
            marker = "✓ suivi" if r.get("followed") else ""
            lines.append(f" {i}. **{r['name']}** {marker}")
            lines.append(f"    `{r['room_id']}`")

        lines.append(
            "\n---\n"
            "Pour suivre un salon : `tchap_search_rooms(follow_room_id=\"!xxx:server\")`\n"
            "Pour chercher : `tchap_search_rooms(query=\"mot-clé\")`\n"
            "Pour retirer : `tchap_search_rooms(unfollow_room_id=\"!xxx:server\")`"
        )

        return "\n".join(lines)

    # ── 3. Lister les salons suivis ──────────────────────────

    async def tchap_rooms(
        self,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        OBLIGATOIRE : Appeler cette fonction quand l'utilisateur demande la liste des salons Tchap. Ne jamais inventer de salons.

        Retourne la liste réelle des salons Tchap configurés avec leurs statistiques.

        :return: Liste des salons — afficher tel quel à l'utilisateur.
        """
        import httpx

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Récupération des salons Tchap...", "done": False}})

        headers = self._user_headers(__user__)
        params = {}
        if __user__:
            params["user_id"] = __user__.get("id", "")

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(
                    f"{self.valves.base_url}/rooms",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                rooms = resp.json()
        except Exception as exc:
            return f"# Erreur\n\nImpossible de récupérer les salons : {exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"{len(rooms)} salon(s) trouvé(s)", "done": True}})

        if not rooms:
            return (
                "Aucun salon Tchap n'est configuré.\n\n"
                "Utilisez **tchap_connect** pour connecter votre compte, "
                "puis **tchap_search_rooms** pour choisir les salons à suivre."
            )

        # Group by owner_type
        personal = [r for r in rooms if r.get("owner_type") == "user"]
        group = [r for r in rooms if r.get("owner_type") == "group"]
        global_rooms = [r for r in rooms if r.get("owner_type") == "global"]
        other = [r for r in rooms if r.get("owner_type") not in ("user", "group", "global")]

        lines = ["# Salons Tchap disponibles\n"]

        def _format_rooms(room_list: list, section: str) -> None:
            if not room_list:
                return
            lines.append(f"## {section}\n")
            for r in room_list:
                synced = r.get("last_synced", "jamais")
                lines.append(
                    f"- **{r['name']}**\n"
                    f"  - ID : `{r['room_id']}`\n"
                    f"  - Messages : {r['message_count']} | Sync : {synced}"
                )
            lines.append("")

        _format_rooms(personal, "Mes salons personnels")
        _format_rooms(group, "Salons de groupes")
        _format_rooms(global_rooms, "Salons globaux")
        _format_rooms(other, "Salons")

        return "\n".join(lines)

    # ── 4. Analyser un salon ─────────────────────────────────

    async def tchap_analyze(
        self,
        room_id: str,
        question: str = "",
        since_hours: int = 0,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        OBLIGATOIRE : Appeler cette fonction pour analyser un salon Tchap. Ne jamais inventer de messages ou d'analyse.

        Synchronise les derniers messages du salon puis retourne les données réelles pour analyse.
        Utiliser tchap_rooms() d'abord pour obtenir le room_id.

        :param room_id: Identifiant du salon (ex: !abc:agent.tchap.gouv.fr). OBLIGATOIRE.
        :param question: Question spécifique (ex: "Quels irritants ?"). Vide = synthèse complète.
        :param since_hours: Fenêtre en heures (0 = 7 jours par défaut, 720 = 30 jours).
        :return: Messages et contexte du salon — analyser ce contenu pour répondre à l'utilisateur.
        """
        import httpx

        if since_hours <= 0:
            since_hours = self.valves.default_since_hours

        headers = self._user_headers(__user__)

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Synchronisation du salon...", "done": False}})

        owner_type, owner_id = await self._find_room_owner(room_id, __user__)

        async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
            # 1. Sync (best effort)
            try:
                await client.post(
                    f"{self.valves.base_url}/sync",
                    json={"room_id": room_id, "owner_type": owner_type, "owner_id": owner_id},
                    headers=headers,
                )
            except Exception:
                pass

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Préparation de l'analyse...", "done": False}})

            # 2. Get summary
            try:
                resp = await client.post(
                    f"{self.valves.base_url}/summary",
                    json={
                        "room_id": room_id,
                        "since_hours": since_hours,
                        "owner_type": owner_type,
                        "owner_id": owner_id,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                summary = resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403:
                    return f"# Accès refusé\n\nLe salon `{room_id}` n'est pas accessible avec votre compte."
                return f"# Erreur\n\nImpossible de récupérer les données : HTTP {exc.response.status_code}"
            except Exception as exc:
                return f"# Erreur\n\nImpossible de récupérer les données : {exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Analyse de {summary.get('message_count', 0)} messages...", "done": True}})

        if summary.get("message_count", 0) == 0:
            return f"# Aucun message\n\nLe salon **{summary.get('room_name', room_id)}** ne contient aucun message sur les {since_hours} dernières heures."

        top_senders = ", ".join(
            f"{s['pseudonym']} ({s['message_count']} msg)"
            for s in summary.get("top_senders", [])[:5]
        )

        context = (
            f"## Contexte — Salon Tchap\n\n"
            f"- **Salon** : {summary['room_name']}\n"
            f"- **Période** : {summary['period']}\n"
            f"- **Messages analysés** : {summary['message_count']}\n"
            f"- **Contributeurs uniques** : {summary['unique_senders']}\n"
            f"- **Top contributeurs** : {top_senders}\n\n"
            f"## Messages du salon\n\n"
            f"```\n{summary['messages_for_llm']}\n```\n\n"
        )

        if question:
            instruction = (
                f"## Instruction\n\n"
                f"En te basant UNIQUEMENT sur les messages ci-dessus, "
                f"réponds à la question suivante en français :\n\n"
                f"**{question}**\n\n"
                f"Consignes :\n"
                f"- Cite des extraits pertinents (avec le pseudonyme de l'auteur)\n"
                f"- Sois factuel, ne déduis pas ce qui n'est pas dit\n"
                f"- Structure ta réponse avec des sections claires\n"
                f"- Si les messages ne permettent pas de répondre, dis-le\n"
            )
        else:
            instruction = (
                f"## Instruction\n\n"
                f"Produis une analyse complète de ce salon en français, structurée ainsi :\n\n"
                f"1. **Résumé exécutif** (3-5 phrases)\n"
                f"2. **Thèmes dominants** (liste numérotée avec contexte)\n"
                f"3. **Irritants principaux** (problèmes, frictions, blocages, plaintes)\n"
                f"4. **Demandes d'action** (requêtes explicites des participants)\n"
                f"5. **Signaux faibles** (points émergents à surveiller)\n"
                f"6. **Synthèse actionnable** :\n"
                f"   - À retenir\n"
                f"   - À faire\n"
                f"   - À surveiller\n\n"
                f"Consignes :\n"
                f"- Utilise les pseudonymes (Utilisateur_1, etc.), jamais les vrais identifiants\n"
                f"- Cite des extraits courts pour illustrer\n"
                f"- Sois factuel et orienté action\n"
                f"- Catégorise avec : irritant, demande, blocage, confusion, proposition, satisfaction, alerte\n"
                f"- N'invente rien, base-toi uniquement sur les messages\n"
            )

        return context + instruction

    async def _find_room_owner(self, room_id: str, user: dict | None) -> tuple[str, str]:
        """Find which owner context to use for a given room."""
        import httpx

        if not user:
            return "global", "global"

        headers = self._user_headers(user)

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(
                    f"{self.valves.base_url}/rooms",
                    headers=headers,
                    params={"user_id": user.get("id", "")},
                )
                resp.raise_for_status()
                rooms = resp.json()
        except Exception:
            return "global", "global"

        for r in rooms:
            if r.get("room_id") == room_id:
                return r.get("owner_type", "global"), r.get("owner_id", "global")

        return "global", "global"

    # ── 5. Admin plateforme ──────────────────────────────────

    async def tchap_admin(
        self,
        action: str,
        target: str = "",
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        OBLIGATOIRE : Appeler cette fonction pour toute action d'administration Tchap. Réservé aux administrateurs.

        Actions : action="status" (état plateforme), action="list-all" (tous les accès), action="set-global" + target="!room_id" (salon global), action="revoke-user" + target="user_uuid" (révoquer accès).

        :param action: status, list-all, set-global, ou revoke-user.
        :param target: room_id ou user_id selon l'action.
        :return: Résultat — afficher tel quel à l'utilisateur.
        """
        import httpx

        if __user__ and __user__.get("role") != "admin":
            return "# Accès refusé\n\nCette commande est réservée aux administrateurs."

        headers = self._user_headers(__user__)

        if action == "status":
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Vérification...", "done": False}})

            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.get(f"{self.valves.base_url}/admin/status", headers=headers)
                    resp.raise_for_status()
                    status = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            lines = [
                "# Tchap — État de la plateforme\n",
                f"- **Configuré** : {'Oui' if status.get('configured') else 'Non'}",
                f"- **Homeserver** : `{status.get('homeserver_url', 'N/A')}`",
                f"- **Compte global** : `{status.get('user_id', 'N/A')}`",
                f"- **Messages stockés** : {status.get('total_messages', 0)}",
                f"- **Comptes configurés** : {status.get('accounts', 0)}",
            ]
            return "\n".join(lines)

        elif action == "list-all":
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Récupération des accès...", "done": False}})

            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.get(f"{self.valves.base_url}/admin/all-access", headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

            entries = result.get("entries", [])
            if not entries:
                return "# Aucun accès configuré"

            lines = ["# Tous les accès configurés\n"]
            for e in entries:
                lines.append(
                    f"- **{e['owner_type']}/{e['owner_id']}**\n"
                    f"  - Compte Matrix : `{e['user_id']}`\n"
                    f"  - Homeserver : `{e['homeserver_url']}`\n"
                    f"  - Configuré par : `{e['configured_by']}`"
                )
            return "\n".join(lines)

        elif action == "set-global":
            if not target:
                return "Erreur : fournissez un room_id dans `target`."
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/admin/set-global",
                        json={"room_id": target}, headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            return f"# ✓ {result.get('message', 'Salon rendu global.')}"

        elif action == "revoke-user":
            if not target:
                return "Erreur : fournissez un owner_id dans `target`."
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(
                        f"{self.valves.base_url}/admin/revoke",
                        json={"owner_type": "user", "owner_id": target}, headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            return f"# ✓ {result.get('message', 'Accès révoqué.')}"

        else:
            return f"Action inconnue : `{action}`. Actions disponibles : status, list-all, set-global, revoke-user."
