"""Tests for output delivery — gist creation + summarized notifications."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models import ModelId
from src.output_delivery import OutputDelivery


class TestCreateGist:
    """Gist creation should shell out to gh CLI and log stderr on failure."""

    @pytest.mark.asyncio
    async def test_successful_gist(self) -> None:
        delivery = OutputDelivery(notifier=AsyncMock())
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"https://gist.github.com/abc123\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            url = await delivery.create_gist("content", "desc")

        assert url == "https://gist.github.com/abc123"

    @pytest.mark.asyncio
    async def test_failed_gist_logs_stderr(self) -> None:
        delivery = OutputDelivery(notifier=AsyncMock())
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"gh: authentication required"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("src.output_delivery.log") as mock_log,
        ):
            url = await delivery.create_gist("content", "desc")

        assert url is None
        mock_log.warning.assert_called_once()
        call_kwargs = mock_log.warning.call_args
        # stderr should be in the log
        assert "authentication required" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self) -> None:
        delivery = OutputDelivery(notifier=AsyncMock())
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            url = await delivery.create_gist("content", "desc")

        assert url is None


class TestSendOutput:
    """send_output should summarize via agent and create gist for long output."""

    @pytest.mark.asyncio
    async def test_short_output_sent_directly(self) -> None:
        notifier = AsyncMock()
        delivery = OutputDelivery(notifier=notifier)
        await delivery.send_output("job1", "short result", prefix="[Job job1] Done:")

        notifier.send.assert_called_once()
        assert "short result" in notifier.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_long_output_creates_gist_stateless(self) -> None:
        """Stateless summarization uses anthropic client, not agent.run_turn."""
        notifier = AsyncMock()
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.content = [AsyncMock(text="Summary bullet points")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        delivery = OutputDelivery(
            notifier=notifier,
            anthropic_client=mock_client,
            model=ModelId.HAIKU,
        )

        long_text = "x" * 600

        with patch.object(delivery, "create_gist", return_value="https://gist.github.com/abc"):
            await delivery.send_output("job1", long_text, prefix="[Job job1] Done:")

        mock_client.messages.create.assert_called_once()
        notifier.send.assert_called_once()
        msg = notifier.send.call_args[0][0]
        assert "https://gist.github.com/abc" in msg
        assert "Summary bullet points" in msg

    @pytest.mark.asyncio
    async def test_long_output_no_client_truncates(self) -> None:
        notifier = AsyncMock()
        delivery = OutputDelivery(notifier=notifier)
        long_text = "x" * 600

        await delivery.send_output("job1", long_text, prefix="[Job job1] Done:")

        notifier.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_gist_failure_still_sends_summary(self) -> None:
        notifier = AsyncMock()
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.content = [AsyncMock(text="Summary")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        delivery = OutputDelivery(
            notifier=notifier,
            anthropic_client=mock_client,
            model=ModelId.HAIKU,
        )

        with patch.object(delivery, "create_gist", return_value=None):
            await delivery.send_output("job1", "x" * 600, prefix="[Job job1] Done:")

        msg = notifier.send.call_args[0][0]
        assert "gist creation failed" in msg
        assert "Summary" in msg

    @pytest.mark.asyncio
    async def test_send_output_stores_result_in_registry(self) -> None:
        """When job_registry is provided, summary and gist_url are stored."""
        from src.jobs import JobRegistry

        registry = JobRegistry()
        registry.start("job1", job_type="ccc", description="test")

        notifier = AsyncMock()
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.content = [AsyncMock(text="Summary text")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        delivery = OutputDelivery(
            notifier=notifier,
            anthropic_client=mock_client,
            model=ModelId.HAIKU,
            job_registry=registry,
        )

        with patch.object(delivery, "create_gist", return_value="https://gist.github.com/abc"):
            await delivery.send_output("job1", "x" * 600, prefix="[Job job1] Done:")

        job = registry.get("job1")
        assert job is not None
        assert job["result_summary"] == "Summary text"
        assert job["gist_url"] == "https://gist.github.com/abc"
