"""Tests for streaming support in Agent.run_turn()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from src.agent import Agent
from src.models import ModelId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> MagicMock:
    """Minimal Settings mock with model defaults."""
    cfg = MagicMock()
    cfg.anthropic_api_key = "sk-test"
    cfg.haiku_model = ModelId.HAIKU
    cfg.default_model = ModelId.SONNET
    cfg.opus_model = ModelId.OPUS
    cfg.max_context_tokens = 120_000
    cfg.session_timeout_hours = 8.0
    cfg.max_autonomous_turns = 10
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_usage(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int | None = None,
    cache_read: int | None = None,
) -> MagicMock:
    """Build a Usage mock with proper isinstance checks for cache tokens."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    # Use real int values so isinstance(x, int) works correctly
    usage.cache_creation_input_tokens = cache_creation if isinstance(cache_creation, int) else None
    usage.cache_read_input_tokens = cache_read if isinstance(cache_read, int) else None
    return usage


def _make_text_block(text: str) -> MagicMock:
    """Create a content block that looks like a TextBlock."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(tool_id: str, name: str, input_data: dict[str, Any]) -> MagicMock:
    """Create a content block that looks like a ToolUseBlock."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    # text attribute should not exist for tool_use
    del block.text
    return block


def _make_response(
    text: str = "Hello!",
    stop_reason: str = "end_turn",
    usage: MagicMock | None = None,
) -> MagicMock:
    """Create a Message-like response."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = [_make_text_block(text)]
    resp.usage = usage or _make_usage()
    return resp


def _make_agent(config: MagicMock | None = None) -> Agent:
    """Build an Agent with all deps mocked out."""
    cfg = config or _make_config()
    guardrails = MagicMock()
    guardrails.run = AsyncMock(return_value=(True, None))
    memory = MagicMock()
    memory.build_context = MagicMock(return_value="")
    memory.append_turn = MagicMock()
    audit = AsyncMock()
    cost_guard = MagicMock()
    cost_guard.daily_cost_usd = 0.0
    cost_guard.monthly_cost_usd = 0.0
    cost_guard.session_tokens = 0
    loop_detector = MagicMock()
    rate_limiter = MagicMock()

    with patch("src.agent.anthropic.AsyncAnthropic"):
        agent = Agent(
            config=cfg,
            guardrails=guardrails,
            memory=memory,
            tool_registry={},
            audit=audit,
            cost_guard=cost_guard,
            loop_detector=loop_detector,
            rate_limiter=rate_limiter,
        )
    return agent


# ---------------------------------------------------------------------------
# Stream mock helpers
# ---------------------------------------------------------------------------


class MockStreamEvent:
    """Simulates a streaming event."""

    def __init__(self, event_type: str, delta_type: str | None = None, delta_text: str | None = None) -> None:
        self.type = event_type
        if delta_type is not None:
            self.delta = MagicMock()
            self.delta.type = delta_type
            self.delta.text = delta_text or ""


class MockAsyncStream:
    """Simulates the async stream object returned by messages.stream().__aenter__()."""

    def __init__(self, events: list[MockStreamEvent], final_message: MagicMock) -> None:
        self._events = events
        self._final_message = final_message

    async def __aenter__(self) -> MockAsyncStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> MockAsyncStream:
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> MockStreamEvent:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self) -> MagicMock:
        return self._final_message


def _make_stream_events(text: str) -> list[MockStreamEvent]:
    """Create a realistic sequence of stream events for a text response."""
    events: list[MockStreamEvent] = []
    events.append(MockStreamEvent("content_block_start"))
    # Split text into chunks to simulate streaming
    for i in range(0, len(text), 5):
        chunk = text[i : i + 5]
        events.append(MockStreamEvent("content_block_delta", delta_type="text_delta", delta_text=chunk))
    events.append(MockStreamEvent("content_block_stop"))
    events.append(MockStreamEvent("message_stop"))
    return events


# ===========================================================================
# Tests
# ===========================================================================


class TestRunTurnWithoutCallback:
    """stream_callback=None should use messages.create() as before."""

    @pytest.mark.asyncio
    async def test_run_turn_without_callback_works_as_before(self) -> None:
        agent = _make_agent()
        response = _make_response(text="Hello world")
        agent._client.messages.create = AsyncMock(return_value=response)

        result = await agent.run_turn("hi")

        assert result == "Hello world"
        agent._client.messages.create.assert_awaited()
        # stream() should NOT be called
        agent._client.messages.stream.assert_not_called()


class TestStreamCallbackCalled:
    """When stream_callback is provided, it should be called with accumulated text."""

    @pytest.mark.asyncio
    async def test_stream_callback_called_with_text(self) -> None:
        agent = _make_agent()
        # First: create() returns a non-tool-use response (triggers streaming re-do)
        create_response = _make_response(text="Hello world")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        # Then: stream re-does the call with streaming
        final_msg = _make_response(text="Hello world")
        events = _make_stream_events("Hello world")
        mock_stream = MockAsyncStream(events, final_msg)
        agent._client.messages.stream = MagicMock(return_value=mock_stream)

        callback = AsyncMock()
        result = await agent.run_turn("hi", stream_callback=callback)

        assert result == "Hello world"
        # Callback should have been called at least once
        assert callback.await_count > 0
        # The last call should have the full accumulated text
        last_call_text = callback.call_args_list[-1][0][0]
        assert last_call_text == "Hello world"

    @pytest.mark.asyncio
    async def test_stream_callback_receives_accumulated_text(self) -> None:
        """Each callback call should receive the full accumulated text so far, not just the delta."""
        agent = _make_agent()
        create_response = _make_response(text="ABCDE")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        final_msg = _make_response(text="ABCDE")
        # Create events with 1-char chunks for precise checking
        events = [
            MockStreamEvent("content_block_start"),
            MockStreamEvent("content_block_delta", delta_type="text_delta", delta_text="A"),
            MockStreamEvent("content_block_delta", delta_type="text_delta", delta_text="B"),
            MockStreamEvent("content_block_delta", delta_type="text_delta", delta_text="C"),
            MockStreamEvent("content_block_delta", delta_type="text_delta", delta_text="D"),
            MockStreamEvent("content_block_delta", delta_type="text_delta", delta_text="E"),
            MockStreamEvent("content_block_stop"),
            MockStreamEvent("message_stop"),
        ]
        mock_stream = MockAsyncStream(events, final_msg)
        agent._client.messages.stream = MagicMock(return_value=mock_stream)

        received: list[str] = []
        callback = AsyncMock(side_effect=lambda text: received.append(text))
        await agent.run_turn("hi", stream_callback=callback)

        # Each call should have progressively longer accumulated text
        assert received == ["A", "AB", "ABC", "ABCD", "ABCDE"]


class TestStreamCallbackNotCalledForToolUse:
    """Tool-use turns should always use messages.create(), never stream."""

    @pytest.mark.asyncio
    async def test_stream_callback_not_called_for_tool_use(self) -> None:
        agent = _make_agent()

        # First response: tool_use (should use create, not stream)
        tool_block = _make_tool_use_block("tool_1", "tasks", {"action": "list"})
        tool_response = MagicMock()
        tool_response.stop_reason = "tool_use"
        tool_response.content = [tool_block]
        tool_response.usage = _make_usage()

        # Second response from create: final text (triggers stream re-do)
        create_text_response = _make_response(text="Here are your tasks")

        # Stream for the final response
        final_msg = _make_response(text="Here are your tasks")
        events = _make_stream_events("Here are your tasks")
        mock_stream = MockAsyncStream(events, final_msg)

        # Set up a tool in the registry
        tool = MagicMock()
        tool.name = "tasks"
        tool.description = "Task management"
        tool.input_schema = {"type": "object", "properties": {}}
        tool.execute = AsyncMock(return_value="task list here")
        agent._tools = {"tasks": tool}

        # First create() returns tool_use, second create() returns text
        agent._client.messages.create = AsyncMock(side_effect=[tool_response, create_text_response])
        agent._client.messages.stream = MagicMock(return_value=mock_stream)

        callback = AsyncMock()
        result = await agent.run_turn("list tasks", stream_callback=callback)

        # messages.create was called (at least for the tool_use turn)
        agent._client.messages.create.assert_awaited()
        # stream was used for the final text response
        agent._client.messages.stream.assert_called()
        # callback was called during the streaming final response
        assert callback.await_count > 0
        assert result == "Here are your tasks"


class TestStreamRetryOnConnectionError:
    """Connection errors during streaming should retry from scratch."""

    @pytest.mark.asyncio
    async def test_stream_retry_on_connection_error(self) -> None:
        agent = _make_agent()
        # create() returns non-tool-use, triggering stream re-do
        create_response = _make_response(text="Success after retry")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        # First stream attempt fails with connection error
        class FailingStream:
            async def __aenter__(self) -> FailingStream:
                raise anthropic.APIConnectionError(request=MagicMock())

            async def __aexit__(self, *args: Any) -> None:
                pass

        # Second attempt succeeds
        final_msg = _make_response(text="Success after retry")
        events = _make_stream_events("Success after retry")
        good_stream = MockAsyncStream(events, final_msg)

        agent._client.messages.stream = MagicMock(side_effect=[FailingStream(), good_stream])

        callback = AsyncMock()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run_turn("hi", stream_callback=callback)

        assert result == "Success after retry"
        assert agent._client.messages.stream.call_count == 2

    @pytest.mark.asyncio
    async def test_stream_exhausts_retries(self) -> None:
        """After 3 failed attempts, return an error message."""
        agent = _make_agent()
        create_response = _make_response(text="whatever")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        class FailingStream:
            async def __aenter__(self) -> FailingStream:
                raise anthropic.APIConnectionError(request=MagicMock())

            async def __aexit__(self, *args: Any) -> None:
                pass

        agent._client.messages.stream = MagicMock(return_value=FailingStream())

        callback = AsyncMock()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run_turn("hi", stream_callback=callback)

        assert "trouble" in result.lower() or "try again" in result.lower()


class TestStreamCostTracking:
    """Cost tracking must work with streaming responses (using get_final_message)."""

    @pytest.mark.asyncio
    async def test_stream_cost_tracking(self) -> None:
        agent = _make_agent()
        # create() returns non-tool-use to trigger streaming
        create_response = _make_response(text="tracked response")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        usage = _make_usage(input_tokens=200, output_tokens=100, cache_creation=50, cache_read=30)
        final_msg = _make_response(text="tracked response", usage=usage)
        events = _make_stream_events("tracked response")
        mock_stream = MockAsyncStream(events, final_msg)
        agent._client.messages.stream = MagicMock(return_value=mock_stream)

        callback = AsyncMock()
        await agent.run_turn("hi", stream_callback=callback)

        # cost_guard.record_llm_call should have been called
        # (once for the stream response — the initial create() response is discarded)
        assert agent._cost_guard.record_llm_call.call_count >= 1
        # The last call should have the streaming response's tokens
        last_call_args = agent._cost_guard.record_llm_call.call_args
        assert last_call_args[0][0] == 200  # input_tokens
        assert last_call_args[0][1] == 100  # output_tokens

    @pytest.mark.asyncio
    async def test_stream_cost_with_no_cache_tokens(self) -> None:
        """Cache tokens that are None (not ints) should default to 0."""
        agent = _make_agent()
        create_response = _make_response(text="no cache")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        usage = _make_usage(input_tokens=100, output_tokens=50, cache_creation=None, cache_read=None)
        final_msg = _make_response(text="no cache", usage=usage)
        events = _make_stream_events("no cache")
        mock_stream = MockAsyncStream(events, final_msg)
        agent._client.messages.stream = MagicMock(return_value=mock_stream)

        callback = AsyncMock()
        await agent.run_turn("hi", stream_callback=callback)

        # Should not crash — cache tokens default to 0
        assert agent._cost_guard.record_llm_call.call_count >= 1

    @pytest.mark.asyncio
    async def test_stream_audit_logging(self) -> None:
        """Streaming responses should log to audit DB."""
        agent = _make_agent()
        create_response = _make_response(text="audited")
        agent._client.messages.create = AsyncMock(return_value=create_response)

        usage = _make_usage(input_tokens=200, output_tokens=100)
        final_msg = _make_response(text="audited", usage=usage)
        events = _make_stream_events("audited")
        mock_stream = MockAsyncStream(events, final_msg)
        agent._client.messages.stream = MagicMock(return_value=mock_stream)

        callback = AsyncMock()
        await agent.run_turn("hi", stream_callback=callback)

        # audit.log_tier2 should have been called for the LLM call
        assert agent._audit.log_tier2.await_count >= 2  # user_message + llm_call
