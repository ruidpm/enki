"""Web search tool — Brave Search via curl."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from typing import Any

import structlog

log = structlog.get_logger()

_CURL = "curl"  # permitted CLI binary
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class WebSearchTool:
    name = "web_search"
    description = "Search the web using Brave Search. Returns top results with title, URL, and snippet."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def execute(self, **kwargs: Any) -> str:
        query = kwargs["query"]
        count = min(int(kwargs.get("count", 5)), 10)
        encoded = urllib.parse.quote_plus(query)
        url = f"{_BRAVE_URL}?q={encoded}&count={count}"

        cmd = [
            _CURL,
            "-sf",
            "--compressed",
            "-H",
            "Accept: application/json",
            "-H",
            f"X-Subscription-Token: {self._api_key}",
            url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"Search failed: {stderr.decode().strip()}"

        try:
            data = json.loads(stdout.decode())
            results = data.get("web", {}).get("results", [])
            if not results:
                return "No results found."
            lines = []
            for r in results[:count]:
                lines.append(f"**{r.get('title', '')}**")
                lines.append(r.get("url", ""))
                lines.append(r.get("description", ""))
                lines.append("")
            return "\n".join(lines).strip()
        except json.JSONDecodeError as e:
            return f"Failed to parse search response: {e}"
