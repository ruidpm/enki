"""Tests for hash chain integrity."""
from __future__ import annotations

import pytest

from src.audit.integrity import compute_data_hash, compute_chain_hash, verify_chain


def make_records(n: int) -> list[dict]:  # type: ignore[type-arg]
    """Build a valid chain of n records matching the DB row format."""
    records = []
    prev_hash = ""
    for i in range(n):
        event_type = "SESSION_START"
        session_id = f"s{i}"
        timestamp = f"2026-01-0{i + 1}T00:00:00"
        data: dict = {"extra": f"info{i}"}  # type: ignore[type-arg]
        # full_data mirrors what db.py hashes
        full_data = {"event_type": event_type, "session_id": session_id,
                     "timestamp": timestamp, **data}
        data_hash = compute_data_hash(full_data)
        chain_hash = compute_chain_hash(prev_hash, data_hash)
        records.append({
            "id": i + 1,
            "event_type": event_type,
            "session_id": session_id,
            "timestamp": timestamp,
            "data": data,
            "data_hash": data_hash,
            "prev_chain_hash": prev_hash,
            "chain_hash": chain_hash,
        })
        prev_hash = chain_hash
    return records


def test_valid_chain_passes() -> None:
    records = make_records(5)
    valid, msg = verify_chain(records)
    assert valid is True
    assert msg == ""


def test_empty_chain_passes() -> None:
    valid, msg = verify_chain([])
    assert valid is True


def test_tampered_record_fails() -> None:
    records = make_records(5)
    records[2]["event_type"] = "TAMPERED"  # modify data without recomputing hash
    valid, msg = verify_chain(records)
    assert valid is False
    assert "record 2" in msg


def test_tampered_hash_fails() -> None:
    records = make_records(3)
    records[1]["chain_hash"] = "deadbeef" * 8
    valid, msg = verify_chain(records)
    assert valid is False


def test_single_record_chain() -> None:
    records = make_records(1)
    valid, _ = verify_chain(records)
    assert valid is True
