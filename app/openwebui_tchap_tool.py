"""
title: Tchap Reader - Analyse de salons Matrix/Tchap
description: Lire et analyser les conversations d'un salon Tchap. Synthèse, tendances, irritants, demandes d'action.
author: tchap-reader
version: 0.1.0
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
            default=120,
            description="Timeout en secondes",
        )
        default_since_hours: int = Field(
            default=168,
            description="Fenêtre temporelle par défaut (heures). 168 = 7 jours.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def tchap_rooms(
        self,
        __event_emitter__=None,
    ) -> str:
        """
        Liste les salons Tchap disponibles et leur activité récente.
        Utile pour savoir quels salons sont suivis et leur volume de messages.

        :return: Liste des salons avec statistiques.
        """
        import httpx

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Récupération des salons Tchap...", "done": False}})

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(f"{self.valves.base_url}/rooms")
                resp.raise_for_status()
                rooms = resp.json()
        except Exception as exc:
            return f"# Erreur\n\nImpossible de récupérer les salons : {exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"{len(rooms)} salon(s) trouvé(s)", "done": True}})

        if not rooms:
            return "Aucun salon Tchap n'est configuré. Vérifiez TCHAP_ALLOWED_ROOM_IDS."

        lines = ["# Salons Tchap disponibles\n"]
        for r in rooms:
            synced = r.get("last_synced", "jamais")
            lines.append(
                f"- **{r['name']}**\n"
                f"  - ID : `{r['room_id']}`\n"
                f"  - Messages stockés : {r['message_count']}\n"
                f"  - Dernière sync : {synced}"
            )
        return "\n".join(lines)

    async def tchap_analyze(
        self,
        room_id: str,
        question: str = "",
        since_hours: int = 0,
        __event_emitter__=None,
    ) -> str:
        """
        Analyse un salon Tchap : synthèse, tendances, irritants, demandes d'action.
        Synchronise d'abord les derniers messages, puis prépare les données pour l'analyse.

        Sans question : produit une synthèse complète.
        Avec question : répond à la question en se basant sur les messages du salon.

        :param room_id: L'identifiant du salon (ex: !abc:agent.tchap.gouv.fr). Utilisez tchap_rooms() pour les trouver.
        :param question: Question optionnelle sur le salon (ex: "Quels sont les irritants ?", "Quelles tendances ?").
        :param since_hours: Fenêtre temporelle en heures (0 = utiliser la valeur par défaut, 168 = 7 jours, 720 = 30 jours).
        :return: Données du salon structurées pour analyse.
        """
        import httpx

        if since_hours <= 0:
            since_hours = self.valves.default_since_hours

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Synchronisation du salon...", "done": False}})

        async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
            # 1. Sync (best effort — use local data if sync fails)
            try:
                await client.post(
                    f"{self.valves.base_url}/sync",
                    json={"room_id": room_id},
                )
            except Exception as exc:
                pass  # Use existing local data

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Préparation de l'analyse...", "done": False}})

            # 2. Get summary
            try:
                resp = await client.post(
                    f"{self.valves.base_url}/summary",
                    json={"room_id": room_id, "since_hours": since_hours},
                )
                resp.raise_for_status()
                summary = resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403:
                    return f"# Accès refusé\n\nLe salon `{room_id}` n'est pas dans la liste des salons autorisés."
                return f"# Erreur\n\nImpossible de récupérer les données : HTTP {exc.response.status_code}"
            except Exception as exc:
                return f"# Erreur\n\nImpossible de récupérer les données : {exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Analyse de {summary.get('message_count', 0)} messages...", "done": True}})

        if summary.get("message_count", 0) == 0:
            return f"# Aucun message\n\nLe salon **{summary.get('room_name', room_id)}** ne contient aucun message sur les {since_hours} dernières heures."

        # 3. Build structured context for the LLM
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

    async def tchap_sync(
        self,
        room_id: str,
        __event_emitter__=None,
    ) -> str:
        """
        Force une synchronisation manuelle d'un salon Tchap.
        Récupère les derniers messages depuis le serveur Matrix.

        :param room_id: L'identifiant du salon à synchroniser.
        :return: Résultat de la synchronisation.
        """
        import httpx

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Synchronisation en cours...", "done": False}})

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.post(
                    f"{self.valves.base_url}/sync",
                    json={"room_id": room_id},
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                return f"Accès refusé — le salon `{room_id}` n'est pas autorisé."
            return f"Erreur de synchronisation : HTTP {exc.response.status_code}"
        except Exception as exc:
            return f"Erreur de synchronisation : {exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Synchronisation terminée", "done": True}})

        return (
            f"Synchronisation terminée pour le salon `{room_id}` :\n"
            f"- Événements traités : {result.get('synced', 0)}\n"
            f"- Nouveaux messages : {result.get('new_messages', 0)}"
        )
