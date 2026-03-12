"""Tests for output delivery — gist creation + summarized notifications."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

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
    async def test_long_output_creates_gist(self) -> None:
        notifier = AsyncMock()
        agent = AsyncMock()
        agent.run_turn = AsyncMock(return_value="Summary bullet points")
        delivery = OutputDelivery(notifier=notifier, agent=agent)

        long_text = "x" * 600

        with patch.object(delivery, "create_gist", return_value="https://gist.github.com/abc"):
            await delivery.send_output("job1", long_text, prefix="[Job job1] Done:")

        notifier.send.assert_called_once()
        msg = notifier.send.call_args[0][0]
        assert "https://gist.github.com/abc" in msg
        assert "Summary bullet points" in msg

    @pytest.mark.asyncio
    async def test_long_output_no_agent_truncates(self) -> None:
        notifier = AsyncMock()
        delivery = OutputDelivery(notifier=notifier)
        long_text = "x" * 600

        await delivery.send_output("job1", long_text, prefix="[Job job1] Done:")

        notifier.send.assert_called_once()
        # No gist, no summary — sent with truncation cap (800 chars of content)
        notifier.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_gist_failure_still_sends_summary(self) -> None:
        notifier = AsyncMock()
        agent = AsyncMock()
        agent.run_turn = AsyncMock(return_value="Summary")
        delivery = OutputDelivery(notifier=notifier, agent=agent)

        with patch.object(delivery, "create_gist", return_value=None):
            await delivery.send_output("job1", "x" * 600, prefix="[Job job1] Done:")

        msg = notifier.send.call_args[0][0]
        assert "gist creation failed" in msg
        assert "Summary" in msg
