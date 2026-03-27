"""
In-memory store for tracking extra notification message IDs per match
and cleanup scheduling.

match_extra_msg_ids: dict[match_id, list[(channel_id, msg_id)]]
    Extra messages generated during a match lifecycle (supplement notices,
    cancel notices, admin-control replies, etc.).

match_ended_at: dict[match_id, datetime]
    When a match was first recorded as ended (cancelled or finished).
    Used by the cleanup task to decide when 6 hours have elapsed.
"""

from datetime import datetime

# dict[int, list[tuple[int, str]]]  – (channel_id, message_id) per match
match_extra_msg_ids: dict = {}

# dict[int, datetime]  – time the match was first seen as ended
match_ended_at: dict = {}


def add_extra_msg(match_id: int, channel_id: int, msg_id: str) -> None:
    """Append (channel_id, msg_id) to the extra-message list for match_id."""
    if match_id not in match_extra_msg_ids:
        match_extra_msg_ids[match_id] = []
    match_extra_msg_ids[match_id].append((channel_id, msg_id))


def get_extra_msgs(match_id: int) -> list:
    """Return a copy of the extra (channel_id, msg_id) list for match_id."""
    return list(match_extra_msg_ids.get(match_id, []))


def set_match_ended(match_id: int, dt: datetime) -> None:
    """Record dt as the time match_id ended (only recorded once)."""
    if match_id not in match_ended_at:
        match_ended_at[match_id] = dt


def get_match_ended(match_id: int) -> "datetime | None":
    """Return the recorded end-time for match_id, or None."""
    return match_ended_at.get(match_id)


def remove_match(match_id: int) -> None:
    """Remove all tracking data for match_id."""
    match_extra_msg_ids.pop(match_id, None)
    match_ended_at.pop(match_id, None)
