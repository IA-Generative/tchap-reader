"""
title: Tchap - Analyse et gestion de salons Matrix/Tchap
description: Configurer l'accès Tchap, lister et analyser les salons. Résultats en HTML scrollable + synthèse LLM.
author: tchapreader
version: 1.0.0
"""

import html as html_mod
import json

from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse


# ── HTML rendering helpers ──────────────────────────────

def _render_rooms_html(rooms: list[dict], title: str, info: str = "") -> str:
    rows = ""
    for r in rooms:
        followed = "✓" if r.get("followed") else ""
        msg_count = r.get("message_count", "")
        last_sync = (r.get("last_synced") or "")[:16]
        name = html_mod.escape(r.get("name", r.get("room_id", "?")))
        rid = html_mod.escape(r.get("room_id", ""))
        rows += f"""<tr>
            <td style="padding:6px 10px;border-bottom:1px solid #eee"><strong>{name}</strong><br><span style="color:#999;font-size:11px">{rid}</span></td>
            <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center">{followed}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:right">{msg_count}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #eee;color:#888;font-size:12px">{last_sync}</td>
        </tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{{font-family:-apple-system,sans-serif;margin:0;padding:12px;background:#fafafa;min-height:100vh}}
h3{{margin:0 0 4px;color:#333;font-size:15px}} .info{{color:#666;font-size:12px;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
th{{background:#f5f5f5;padding:8px 10px;text-align:left;font-size:13px;border-bottom:2px solid #ddd}}
</style></head><body>
<h3>{html_mod.escape(title)}</h3>
{f'<div class="info">{html_mod.escape(info)}</div>' if info else ''}
<table><tr><th>Salon</th><th>Suivi</th><th>Messages</th><th>Dernière sync</th></tr>
{rows}</table></body></html>"""


def _render_messages_html(messages: list[dict], room_name: str, info: str = "") -> str:
    rows = ""
    for m in messages:
        sender = html_mod.escape(m.get("sender", "?"))
        ts = m.get("timestamp", "")
        # Format timestamp
        if isinstance(ts, (int, float)):
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, tz=timezone.utc).strftime("%d/%m %H:%M")
        body = html_mod.escape(m.get("body", ""))
        reply = " 💬" if m.get("reply_to") else ""
        edit = " ✏️" if m.get("is_edit") else ""
        rows += f"""<tr>
            <td style="padding:4px 8px;border-bottom:1px solid #eee;white-space:nowrap;color:#666;font-size:11px;vertical-align:top">{ts}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #eee;font-weight:600;white-space:nowrap;vertical-align:top;font-size:12px">{sender}{reply}{edit}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #eee;font-size:12px;word-break:break-word">{body}</td>
        </tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{{font-family:-apple-system,sans-serif;margin:0;padding:12px;background:#fafafa;min-height:100vh}}
h3{{margin:0 0 4px;color:#333;font-size:15px}} .info{{color:#666;font-size:12px;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
th{{background:#f5f5f5;padding:6px 8px;text-align:left;font-size:12px;border-bottom:2px solid #ddd}}
</style></head><body>
<h3>💬 {html_mod.escape(room_name)}</h3>
{f'<div class="info">{html_mod.escape(info)}</div>' if info else ''}
<table><tr><th>Date</th><th>Auteur</th><th>Message</th></tr>
{rows}</table></body></html>"""


# ── Tool class ──────────────────────────────────────────

class Tools:
    class Valves(BaseModel):
        base_url: str = Field(default="http://host.docker.internal:8087", description="URL du service tchapreader")
        timeout: int = Field(default=120, description="Timeout en secondes")
        default_since_hours: int = Field(default=168, description="Fenêtre temporelle par défaut (heures). 168 = 7 jours.")

    class UserValves(BaseModel):
        tchap_email: str = Field(default="", description="Votre email Tchap (ex: prenom.nom@interieur.gouv.fr)")
        tchap_password: str = Field(default="", description="Votre mot de passe Tchap (masqué)", json_schema_extra={"format": "password"})
        tchap_token: str = Field(default="", description="OU votre token d'accès Matrix (mct_xxx ou syt_xxx)", json_schema_extra={"format": "password"})

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

    def _get_user_valves(self, user: dict | None) -> dict:
        if not user:
            return {}
        valves = user.get("valves", {})
        if valves and hasattr(valves, "model_dump"):
            return valves.model_dump()
        if valves and not isinstance(valves, dict):
            return {k: getattr(valves, k, "") for k in ("tchap_email", "tchap_password", "tchap_token") if hasattr(valves, k)}
        return valves or {}

    # ── 1. Connexion ────────────────────────────────────────

    async def tchap_connect(self, __user__: dict = None, __event_emitter__=None) -> str:
        """OBLIGATOIRE : Appeler cette fonction quand l'utilisateur veut se connecter ou configurer Tchap.

        Les credentials sont lus depuis les paramètres utilisateur du tool (icône engrenage).
        :return: Résultat de la connexion — afficher tel quel.
        """
        import httpx

        owner_type, owner_id = "user", (__user__ or {}).get("id", "")
        headers = self._user_headers(__user__)
        uv = self._get_user_valves(__user__)
        tchap_token = uv.get("tchap_token", "")
        tchap_email = uv.get("tchap_email", "")
        tchap_password = uv.get("tchap_password", "")

        # Check already connected
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.valves.base_url}/rooms", headers=headers, params={"user_id": owner_id})
                if resp.status_code == 200 and resp.json():
                    if not tchap_token and not tchap_email:
                        return "# Déjà connecté ✓\n\nVotre compte Tchap est configuré."
        except Exception:
            pass

        # Token login
        if tchap_token:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Vérification du token...", "done": False}})
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(f"{self.valves.base_url}/setup/login-token",
                        json={"token": tchap_token, "owner_type": owner_type, "owner_id": owner_id}, headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})
            if result.get("ok"):
                return f"# Connecté ✓\n\nCompte : `{result.get('user_id', '')}`"
            return f"# Token invalide\n\n{result.get('message', '')}"

        # Password login
        if tchap_email and tchap_password:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Connexion...", "done": False}})
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(f"{self.valves.base_url}/setup/login-password",
                        json={"email": tchap_email, "password": tchap_password, "owner_type": owner_type, "owner_id": owner_id}, headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})
            if result.get("ok"):
                return f"# Connecté ✓\n\nCompte : `{result.get('user_id', '')}`\n\n*Vous pouvez effacer votre mot de passe des paramètres du tool.*"
            return f"# Échec\n\n{result.get('message', '')}"

        return ("# Configuration requise\n\n"
            "Renseignez vos credentials dans les **paramètres du tool** (icône engrenage) :\n"
            "- **Token** (mct_xxx) OU **Email + Mot de passe**\n"
            "Puis dites « connecte-moi à Tchap ».")

    # ── 2. Recherche de salons ──────────────────────────────

    async def tchap_search_rooms(self, query: str = "", follow_room_id: str = "", unfollow_room_id: str = "",
                                  __user__: dict = None, __event_emitter__=None):
        """Chercher, suivre ou retirer des salons Tchap.

        :param query: Mot-clé pour filtrer (vide = tous les salons)
        :param follow_room_id: Room ID à suivre
        :param unfollow_room_id: Room ID à ne plus suivre
        """
        import httpx

        owner_type, owner_id = "user", (__user__ or {}).get("id", "")
        headers = self._user_headers(__user__)

        # Follow/unfollow actions → return markdown
        if follow_room_id or unfollow_room_id:
            action = "follow-room" if follow_room_id else "unfollow-room"
            rid = follow_room_id or unfollow_room_id
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(f"{self.valves.base_url}/{action}",
                        json={"room_id": rid, "owner_type": owner_type, "owner_id": owner_id}, headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            if result.get("ok"):
                followed = result.get("followed_rooms", [])
                verb = "ajouté" if follow_room_id else "retiré"
                return f"# Salon {verb} ✓\n\n{result.get('message', '')}\n\nSalons suivis ({len(followed)})"
            return f"# Erreur\n\n{result.get('message', '')}"

        # Search → HTMLResponse
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Recherche{' : ' + query if query else ''}...", "done": False}})

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(f"{self.valves.base_url}/search-rooms",
                    params={"q": query, "owner_type": owner_type, "owner_id": owner_id}, headers=headers)
                resp.raise_for_status()
                result = resp.json()
        except Exception as exc:
            return f"# Erreur\n\n{exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "OK", "done": True}})

        if not result.get("ok"):
            return f"# Erreur\n\n{result.get('message', 'Utilisez tchap_connect.')}"

        rooms = result.get("rooms", [])
        total = result.get("total", 0)
        followed_count = sum(1 for r in rooms if r.get("followed"))

        title = f"Recherche : {query}" if query else "Tous les salons"
        info = f"{len(rooms)} résultats sur {total} — {followed_count} suivis"

        html_content = _render_rooms_html(rooms, title, info)
        context = {
            "total": total,
            "displayed": len(rooms),
            "followed": followed_count,
            "query": query,
            "rooms_summary": [{"name": r["name"], "followed": r.get("followed", False)} for r in rooms[:20]],
            "_instructions": "Le tableau des salons est affiché à l'utilisateur. Fais une synthèse : combien de salons, lesquels sont suivis, propose de suivre les plus pertinents avec tchap_search_rooms(follow_room_id='...').",
        }

        return (HTMLResponse(content=html_content, headers={"Content-Disposition": "inline"}), context)

    # ── 3. Salons suivis ────────────────────────────────────

    async def tchap_rooms(self, __user__: dict = None, __event_emitter__=None):
        """Liste les salons Tchap configurés avec leurs statistiques.

        :return: Tableau des salons suivis avec nombre de messages et dernière synchronisation.
        """
        import httpx

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Récupération des salons...", "done": False}})

        headers = self._user_headers(__user__)
        params = {"user_id": (__user__ or {}).get("id", "")}

        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(f"{self.valves.base_url}/rooms", headers=headers, params=params)
                resp.raise_for_status()
                rooms = resp.json()
        except Exception as exc:
            return f"# Erreur\n\n{exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"{len(rooms)} salon(s)", "done": True}})

        if not rooms:
            return "Aucun salon suivi.\n\nUtilisez **tchap_connect** puis **tchap_search_rooms** pour suivre des salons."

        total_messages = sum(r.get("message_count", 0) for r in rooms)
        title = "Salons Tchap suivis"
        info = f"{len(rooms)} salons — {total_messages} messages au total"

        html_content = _render_rooms_html(rooms, title, info)
        context = {
            "count": len(rooms),
            "total_messages": total_messages,
            "rooms": [{"name": r["name"], "message_count": r.get("message_count", 0), "last_synced": r.get("last_synced")} for r in rooms],
            "_instructions": "Le tableau est affiché. Synthétise : combien de salons, lesquels sont les plus actifs (par nombre de messages). Propose d'analyser les salons actifs avec tchap_analyze(room_id='...').",
        }

        return (HTMLResponse(content=html_content, headers={"Content-Disposition": "inline"}), context)

    # ── 4. Analyse d'un salon ───────────────────────────────

    async def tchap_analyze(self, room_id: str, question: str = "", since_hours: int = 0,
                             __user__: dict = None, __event_emitter__=None):
        """Synchronise et analyse un salon Tchap. Le LLM reçoit les messages pour produire une synthèse intelligente.

        :param room_id: Identifiant du salon (ex: !abc:agent.tchap.gouv.fr). OBLIGATOIRE.
        :param question: Question spécifique. Exemples : "messages importants", "messages sans réponse", "mécontentement", "points positifs". Vide = synthèse complète.
        :param since_hours: Fenêtre en heures (0 = 7 jours par défaut, 720 = 30 jours).
        """
        import httpx

        if since_hours <= 0:
            since_hours = self.valves.default_since_hours

        headers = self._user_headers(__user__)
        owner_type, owner_id = await self._find_room_owner(room_id, __user__)

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Synchronisation du salon...", "done": False}})

        async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
            # Sync
            try:
                await client.post(f"{self.valves.base_url}/sync",
                    json={"room_id": room_id, "owner_type": owner_type, "owner_id": owner_id}, headers=headers)
            except Exception:
                pass

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Récupération des messages...", "done": False}})

            # Get messages for HTML display
            try:
                msg_resp = await client.post(f"{self.valves.base_url}/messages",
                    json={"room_id": room_id, "since_hours": since_hours, "limit": 200, "owner_type": owner_type, "owner_id": owner_id},
                    headers=headers)
                msg_resp.raise_for_status()
                messages_data = msg_resp.json()
            except Exception:
                messages_data = {"messages": [], "total": 0}

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyse...", "done": False}})

            # Get summary for LLM context
            try:
                sum_resp = await client.post(f"{self.valves.base_url}/summary",
                    json={"room_id": room_id, "since_hours": since_hours, "owner_type": owner_type, "owner_id": owner_id},
                    headers=headers)
                sum_resp.raise_for_status()
                summary = sum_resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403:
                    return f"# Accès refusé\n\nLe salon `{room_id}` n'est pas accessible."
                return f"# Erreur HTTP {exc.response.status_code}"
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"{summary.get('message_count', 0)} messages analysés", "done": True}})

        if summary.get("message_count", 0) == 0:
            return f"# Aucun message\n\nLe salon **{summary.get('room_name', room_id)}** est vide sur les {since_hours}h."

        # HTML: timeline of messages
        messages = messages_data.get("messages", [])
        room_name = summary.get("room_name", room_id)
        info = f"{summary['message_count']} messages — {summary['unique_senders']} contributeurs — {summary['period']}"
        html_content = _render_messages_html(messages, room_name, info)

        # Context for LLM
        top_senders = ", ".join(f"{s['pseudonym']} ({s['message_count']} msg)" for s in summary.get("top_senders", [])[:5])

        if question:
            instruction = (
                f"En te basant UNIQUEMENT sur les messages ci-dessous, réponds à : **{question}**\n"
                f"Cite des extraits avec le pseudonyme. Sois factuel."
            )
        else:
            instruction = (
                "Produis une analyse complète :\n"
                "1. **Résumé exécutif** (3-5 phrases)\n"
                "2. **Messages importants** (décisions, annonces, demandes)\n"
                "3. **Messages sans réponse** (questions restées ouvertes)\n"
                "4. **Irritants** (problèmes, mécontentement, blocages)\n"
                "5. **Points positifs** (satisfaction, remerciements)\n"
                "6. **Actions à mener**\n\n"
                "Utilise les pseudonymes, cite des extraits courts."
            )

        context = {
            "room_name": room_name,
            "period": summary.get("period", ""),
            "message_count": summary.get("message_count", 0),
            "unique_senders": summary.get("unique_senders", 0),
            "top_senders": top_senders,
            "messages_for_llm": summary.get("messages_for_llm", ""),
            "_instructions": (
                f"La timeline des messages est affichée à l'utilisateur dans un tableau scrollable.\n\n"
                f"{instruction}\n\n"
                f"Consignes : pseudonymise les noms (Utilisateur_1, etc.), ne fabrique rien, base-toi sur les messages fournis."
            ),
        }

        return (HTMLResponse(content=html_content, headers={"Content-Disposition": "inline"}), context)

    async def _find_room_owner(self, room_id: str, user: dict | None) -> tuple[str, str]:
        import httpx
        if not user:
            return "global", "global"
        headers = self._user_headers(user)
        try:
            async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                resp = await client.get(f"{self.valves.base_url}/rooms", headers=headers,
                    params={"user_id": user.get("id", "")})
                resp.raise_for_status()
                for r in resp.json():
                    if r.get("room_id") == room_id:
                        return r.get("owner_type", "global"), r.get("owner_id", "global")
        except Exception:
            pass
        return "global", "global"

    # ── 5. Admin ────────────────────────────────────────────

    async def tchap_admin(self, action: str, target: str = "", __user__: dict = None, __event_emitter__=None) -> str:
        """Administration Tchap. Réservé aux administrateurs.

        :param action: status, list-all, set-global, ou revoke-user.
        :param target: room_id ou user_id selon l'action.
        """
        import httpx

        if __user__ and __user__.get("role") != "admin":
            return "# Accès refusé\n\nRéservé aux administrateurs."

        headers = self._user_headers(__user__)

        if action == "status":
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.get(f"{self.valves.base_url}/admin/status", headers=headers)
                    resp.raise_for_status()
                    s = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            return (f"# État Tchap\n\n"
                f"- Configuré : {'Oui' if s.get('configured') else 'Non'}\n"
                f"- Homeserver : `{s.get('homeserver_url', 'N/A')}`\n"
                f"- Compte : `{s.get('user_id', 'N/A')}`\n"
                f"- Messages : {s.get('total_messages', 0)}\n"
                f"- Comptes : {s.get('accounts', 0)}")

        elif action == "list-all":
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.get(f"{self.valves.base_url}/admin/all-access", headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
            except Exception as exc:
                return f"# Erreur\n\n{exc}"
            entries = result.get("entries", [])
            if not entries:
                return "# Aucun accès configuré"
            lines = ["# Accès configurés\n"]
            for e in entries:
                lines.append(f"- **{e['owner_type']}/{e['owner_id']}** — `{e['user_id']}` sur `{e['homeserver_url']}`")
            return "\n".join(lines)

        elif action == "set-global":
            if not target:
                return "Erreur : fournissez un room_id dans `target`."
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(f"{self.valves.base_url}/admin/set-global", json={"room_id": target}, headers=headers)
                    resp.raise_for_status()
                    return f"# ✓ {resp.json().get('message', 'OK')}"
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

        elif action == "revoke-user":
            if not target:
                return "Erreur : fournissez un owner_id dans `target`."
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    resp = await client.post(f"{self.valves.base_url}/admin/revoke",
                        json={"owner_type": "user", "owner_id": target}, headers=headers)
                    resp.raise_for_status()
                    return f"# ✓ {resp.json().get('message', 'OK')}"
            except Exception as exc:
                return f"# Erreur\n\n{exc}"

        return f"Action inconnue : `{action}`. Disponibles : status, list-all, set-global, revoke-user."
