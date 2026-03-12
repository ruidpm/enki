"""Unit tests for JobRegistry token tracking."""

from __future__ import annotations

from src.jobs import JobRegistry
from src.models import ModelId


def _start(registry: JobRegistry, job_id: str = "abc123", model: str = ModelId.HAIKU) -> None:
    registry.start(job_id, job_type="pipeline", description="test job", model=model)


def test_tokens_start_at_zero() -> None:
    r = JobRegistry()
    _start(r)
    job = r.get("abc123")
    assert job is not None
    assert job["tokens_total"] == 0
    assert job["cost_usd"] == 0.0


def test_add_tokens_accumulates() -> None:
    r = JobRegistry()
    _start(r)
    r.add_tokens("abc123", input_tokens=1000, output_tokens=200)
    r.add_tokens("abc123", input_tokens=500, output_tokens=100)
    job = r.get("abc123")
    assert job is not None
    assert job["tokens_total"] == 1800


def test_add_tokens_unknown_job_is_noop() -> None:
    r = JobRegistry()
    r.add_tokens("nonexistent", input_tokens=100, output_tokens=50)  # should not raise


def test_cost_usd_haiku() -> None:
    r = JobRegistry()
    _start(r, model=ModelId.HAIKU)
    r.add_tokens("abc123", input_tokens=1_000_000, output_tokens=0)
    job = r.get("abc123")
    assert job is not None
    assert abs(job["cost_usd"] - 0.80) < 0.01


def test_cost_usd_sonnet() -> None:
    r = JobRegistry()
    _start(r, model=ModelId.SONNET)
    r.add_tokens("abc123", input_tokens=1_000_000, output_tokens=0)
    job = r.get("abc123")
    assert job is not None
    assert abs(job["cost_usd"] - 3.00) < 0.01


def test_cost_usd_unknown_model_defaults_to_haiku() -> None:
    r = JobRegistry()
    _start(r, model="unknown-model-xyz")
    r.add_tokens("abc123", input_tokens=1_000_000, output_tokens=0)
    job = r.get("abc123")
    assert job is not None
    assert abs(job["cost_usd"] - 0.80) < 0.01


def test_tokens_visible_in_list_running() -> None:
    r = JobRegistry()
    _start(r)
    r.add_tokens("abc123", input_tokens=500, output_tokens=100)
    jobs = r.list_running()
    assert len(jobs) == 1
    assert jobs[0]["tokens_total"] == 600


# ---------------------------------------------------------------------------
# set_result — result_summary + gist_url storage
# ---------------------------------------------------------------------------


def test_set_result_stores_summary_and_gist_url() -> None:
    r = JobRegistry()
    _start(r)
    r.set_result("abc123", summary="3 bugs fixed", gist_url="https://gist.github.com/xyz")
    job = r.get("abc123")
    assert job is not None
    assert job["result_summary"] == "3 bugs fixed"
    assert job["gist_url"] == "https://gist.github.com/xyz"


def test_set_result_summary_only() -> None:
    r = JobRegistry()
    _start(r)
    r.set_result("abc123", summary="done")
    job = r.get("abc123")
    assert job is not None
    assert job["result_summary"] == "done"
    assert job["gist_url"] is None


def test_set_result_unknown_job_is_noop() -> None:
    r = JobRegistry()
    r.set_result("nonexistent", summary="nope")  # should not raise


def test_result_fields_default_to_none() -> None:
    r = JobRegistry()
    _start(r)
    job = r.get("abc123")
    assert job is not None
    assert job["result_summary"] is None
    assert job["gist_url"] is None
