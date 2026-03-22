"""Prepare message data for LLM analysis — pseudonymization, formatting, stats (multi-tenant)."""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone

from app.config import settings
from app.database import Database

logger = logging.getLogger(__name__)


class SummaryService:
    """Prepares room messages for LLM consumption — multi-tenant aware."""

    def __init__(self, db: Database):
        self._db = db

    def get_summary(
        self,
        room_id: str,
        room_name: str,
        since_hours: int = 168,
        max_messages: int = 500,
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> dict:
        """Build a compact summary for the LLM.

        Returns a dict with stats and a formatted text block.
        """
        # Clamp window
        max_hours = settings.TCHAP_MAX_WINDOW_DAYS * 24
        since_hours = min(since_hours, max_hours)

        now_ms = int(time.time() * 1000)
        since_ms = now_ms - (since_hours * 3600 * 1000)

        messages = self._db.get_messages(
            room_id=room_id,
            since_ts=since_ms,
            until_ts=now_ms,
            limit=min(max_messages, settings.TCHAP_MAX_MESSAGES_PER_ANALYSIS),
            owner_type=owner_type,
            owner_id=owner_id,
        )

        if not messages:
            return {
                "room_id": room_id,
                "room_name": room_name,
                "period": f"dernières {since_hours}h",
                "message_count": 0,
                "unique_senders": 0,
                "top_senders": [],
                "messages_for_llm": "Aucun message dans cette période.",
            }

        # Pseudonymize senders
        sender_map: dict[str, str] = {}
        if settings.TCHAP_ANONYMIZE_OUTPUT:
            unique_senders = sorted(set(m["sender"] for m in messages))
            for i, sender in enumerate(unique_senders, 1):
                sender_map[sender] = f"Utilisateur_{i}"
        else:
            for m in messages:
                sender_map[m["sender"]] = m["sender"]

        # Stats
        sender_counts = Counter(m["sender"] for m in messages)
        top_senders = [
            {
                "pseudonym": sender_map.get(s, s),
                "message_count": c,
            }
            for s, c in sender_counts.most_common(10)
        ]

        # Format messages for LLM
        formatted_lines = []
        for m in messages:
            ts = datetime.fromtimestamp(m["timestamp"] / 1000, tz=timezone.utc)
            ts_str = ts.strftime("%Y-%m-%d %H:%M")
            pseudo = sender_map.get(m["sender"], m["sender"])
            body = m["body"].replace("\n", " ").strip()
            # Truncate very long messages
            if len(body) > 500:
                body = body[:500] + "…"
            reply_marker = ""
            if m.get("reply_to_event_id"):
                reply_marker = " [réponse]"
            formatted_lines.append(f"[{ts_str}] {pseudo}{reply_marker}: {body}")

        messages_text = "\n".join(formatted_lines)

        # Truncate overall if too long (target ~3000 tokens ≈ 12000 chars)
        if len(messages_text) > 12000:
            messages_text = messages_text[:12000] + "\n\n[… tronqué — trop de messages]"

        # Period description
        first_ts = datetime.fromtimestamp(messages[0]["timestamp"] / 1000, tz=timezone.utc)
        last_ts = datetime.fromtimestamp(messages[-1]["timestamp"] / 1000, tz=timezone.utc)
        period = f"{first_ts.strftime('%d/%m/%Y %H:%M')} → {last_ts.strftime('%d/%m/%Y %H:%M')}"

        return {
            "room_id": room_id,
            "room_name": room_name,
            "period": period,
            "message_count": len(messages),
            "unique_senders": len(sender_map),
            "top_senders": top_senders,
            "messages_for_llm": messages_text,
        }
