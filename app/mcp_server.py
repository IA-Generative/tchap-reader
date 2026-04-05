"""
MCP server for tchapreader — Streamable HTTP transport.

Uses the mcp SDK's StreamableHTTPSessionManager directly,
integrated into FastAPI's lifespan for proper task group init.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("tchapreader")


@mcp.tool()
async def tchap_connect() -> str:
    """Connecte l'utilisateur à Tchap via SSO. Retourne les instructions de connexion."""
    return "Pour te connecter à Tchap, utilise la commande SSO dans l'interface. L'administrateur doit configurer TCHAP_ACCESS_TOKEN dans les secrets K8s."


@mcp.tool()
async def tchap_list_rooms() -> str:
    """Liste les salons Tchap suivis par l'utilisateur."""
    from app.api import _db
    try:
        rooms = _db.list_rooms()
        if not rooms:
            return "Aucun salon suivi."
        lines = [f"- {r.get('name', r.get('room_id', '?'))} ({r.get('room_id', '?')})" for r in rooms]
        return "\n".join(lines)
    except Exception as e:
        return f"Erreur : {e}"


@mcp.tool()
async def tchap_discover_rooms(limit: int = 20) -> str:
    """Découvre les salons publics disponibles sur Tchap.

    :param limit: Nombre max de salons à retourner
    """
    from app.api import _default_client
    try:
        rooms = await _default_client.discover_public_rooms(limit=limit)
        if not rooms:
            return "Aucun salon public trouvé."
        lines = [f"- {r.get('name', '?')} ({r.get('room_id', '?')})" for r in rooms]
        return "\n".join(lines)
    except Exception as e:
        return f"Erreur : {e}"


@mcp.tool()
async def tchap_search_rooms(query: str) -> str:
    """Recherche des salons Tchap par mot-clé.

    :param query: Mot-clé de recherche
    """
    from app.api import _default_client
    try:
        rooms = await _default_client.search_rooms(query=query)
        if not rooms:
            return f"Aucun salon trouvé pour '{query}'."
        lines = [f"- {r.get('name', '?')} ({r.get('room_id', '?')})" for r in rooms]
        return "\n".join(lines)
    except Exception as e:
        return f"Erreur : {e}"


@mcp.tool()
async def tchap_get_messages(room_id: str, window_hours: int = 168, keyword: str = "") -> str:
    """Récupère les messages d'un salon Tchap.

    :param room_id: ID du salon Tchap
    :param window_hours: Fenêtre de temps en heures
    :param keyword: Filtrer par mot-clé (optionnel)
    """
    from app.api import _db
    try:
        messages = _db.get_messages(room_id, window_hours=window_hours, keyword=keyword or None)
        if not messages:
            return "Aucun message trouvé."
        lines = [f"[{m.get('timestamp', '?')}] {m.get('sender', '?')}: {m.get('body', '')}" for m in messages[:50]]
        return "\n".join(lines)
    except Exception as e:
        return f"Erreur : {e}"


@mcp.tool()
async def tchap_summarize(room_id: str, window_hours: int = 168) -> str:
    """Résume les messages d'un salon Tchap sur une période donnée.

    :param room_id: ID du salon Tchap
    :param window_hours: Fenêtre de temps en heures
    """
    from app.api import _db, _summary
    try:
        result = await _summary.summarize(room_id, window_hours=window_hours)
        return result.get("summary", "Résumé indisponible.")
    except Exception as e:
        return f"Erreur : {e}"
