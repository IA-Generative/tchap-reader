"""Realistic mock Matrix API responses for testing."""

import time

NOW_MS = int(time.time() * 1000)


def make_text_event(event_id: str, sender: str, body: str, ts: int = NOW_MS) -> dict:
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "content": {"msgtype": "m.text", "body": body},
    }


def make_edit_event(event_id: str, sender: str, new_body: str, original_id: str, ts: int = NOW_MS) -> dict:
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "content": {
            "msgtype": "m.text",
            "body": f"* {new_body}",
            "m.new_content": {"msgtype": "m.text", "body": new_body},
            "m.relates_to": {"rel_type": "m.replace", "event_id": original_id},
        },
    }


def make_reply_event(event_id: str, sender: str, body: str, reply_to_id: str, ts: int = NOW_MS) -> dict:
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "content": {
            "msgtype": "m.text",
            "body": f"> original message\n\n{body}",
            "m.relates_to": {"m.in_reply_to": {"event_id": reply_to_id}},
        },
    }


def make_redaction_event(event_id: str, sender: str, redacts: str, ts: int = NOW_MS) -> dict:
    return {
        "type": "m.room.redaction",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "redacts": redacts,
    }


ROOM_ID = "!testroom:agent.tchap.gouv.fr"
ROOM_NAME = "Salon Test"

SYNC_RESPONSE_WITH_MESSAGES = {
    "next_batch": "s123_456",
    "rooms": {
        "join": {
            ROOM_ID: {
                "timeline": {
                    "events": [
                        make_text_event("$evt1", "@alice:agent.tchap.gouv.fr", "Bonjour, le nouveau processus de validation est trop lent", NOW_MS - 3600_000),
                        make_text_event("$evt2", "@bob:agent.tchap.gouv.fr", "Oui c'est un vrai problème, ça bloque toute l'équipe", NOW_MS - 3500_000),
                        make_text_event("$evt3", "@alice:agent.tchap.gouv.fr", "Il faudrait revoir le workflow d'approbation", NOW_MS - 3000_000),
                        make_reply_event("$evt4", "@charlie:agent.tchap.gouv.fr", "Je suis d'accord, on devrait en parler en réunion", "$evt3", NOW_MS - 2500_000),
                        make_text_event("$evt5", "@bob:agent.tchap.gouv.fr", "L'outil X plante régulièrement depuis la mise à jour", NOW_MS - 2000_000),
                        make_text_event("$evt6", "@alice:agent.tchap.gouv.fr", "Pareil chez nous, 3 crashes cette semaine", NOW_MS - 1500_000),
                        make_text_event("$evt7", "@charlie:agent.tchap.gouv.fr", "La documentation n'est pas à jour non plus", NOW_MS - 1000_000),
                        make_text_event("$evt8", "@diana:agent.tchap.gouv.fr", "Quelqu'un a un contact au support technique ?", NOW_MS - 500_000),
                        make_edit_event("$evt9", "@bob:agent.tchap.gouv.fr", "L'outil X plante 5 fois par jour", "$evt5", NOW_MS - 400_000),
                        make_text_event("$evt10", "@alice:agent.tchap.gouv.fr", "Bonne nouvelle : le correctif est prévu pour vendredi", NOW_MS - 100_000),
                    ],
                }
            }
        }
    },
}

SYNC_RESPONSE_EMPTY = {
    "next_batch": "s789_012",
    "rooms": {"join": {ROOM_ID: {"timeline": {"events": []}}}},
}

SYNC_RESPONSE_WITH_REDACTION = {
    "next_batch": "s345_678",
    "rooms": {
        "join": {
            ROOM_ID: {
                "timeline": {
                    "events": [
                        make_text_event("$evt20", "@alice:agent.tchap.gouv.fr", "Message à supprimer"),
                        make_redaction_event("$evt21", "@alice:agent.tchap.gouv.fr", "$evt20"),
                    ],
                }
            }
        }
    },
}

JOINED_ROOMS_RESPONSE = {"joined_rooms": [ROOM_ID]}

ROOM_NAME_RESPONSE = {"name": ROOM_NAME}
