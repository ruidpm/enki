"""Send a proactive message to the user mid-turn."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.interfaces.notifier import Notifier


class SendMessageTool:
    name = "send_message"
    description = (
        "Send a message to the user immediately, without waiting for the turn to finish. "
        "Use before starting any task that requires 2+ tool calls or takes time: "
        "send a one-line ack first ('Searching now.' / 'On it.' / 'Pipeline starting.'), "
        "then do the work, then return the full answer normally. "
        "Do NOT use for the final answer -- just return that normally."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Short message to send now (under 10 words)",
            }
        },
        "required": ["message"],
    }

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def execute(self, **kwargs: Any) -> str:
        await self._notifier.send(kwargs["message"])
        return "Message sent."
