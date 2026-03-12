"""Hash chain integrity for Tier 1 audit events."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_data_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def compute_chain_hash(prev_chain_hash: str, data_hash: str) -> str:
    return hashlib.sha256(f"{prev_chain_hash}{data_hash}".encode()).hexdigest()


def verify_chain(records: list[dict[str, Any]]) -> tuple[bool, str]:
    """
    Verify the hash chain for a sequence of Tier 1 records.
    Returns (valid, error_message). error_message is empty if valid.
    Records must be ordered by rowid ascending.

    Each record must have the stored data_hash + chain_hash from the DB.
    We verify: chain_hash == SHA256(prev_chain_hash + data_hash).
    Separately, we verify data_hash by reconstructing from event fields.
    """
    prev_hash = ""
    for i, record in enumerate(records):
        # Reconstruct the original payload that was hashed in db.py:
        # full_data = {event_type, session_id, timestamp, **data_kwargs}
        event_data = record.get("data", {})
        if isinstance(event_data, str):
            import json as _json

            event_data = _json.loads(event_data)
        full_data = {
            "event_type": record["event_type"],
            "session_id": record["session_id"],
            "timestamp": record["timestamp"],
            **event_data,
        }
        expected_data_hash = compute_data_hash(full_data)
        if record.get("data_hash") != expected_data_hash:
            return False, (f"Data tampered at record {i} (event={record.get('event_type')}, session={record.get('session_id')})")
        expected_chain = compute_chain_hash(prev_hash, expected_data_hash)
        if record.get("chain_hash") != expected_chain:
            return False, (f"Chain broken at record {i} (event={record.get('event_type')}, session={record.get('session_id')})")
        prev_hash = expected_chain
    return True, ""
