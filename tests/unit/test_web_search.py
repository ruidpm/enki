"""Tests for web search tool (src/tools/web_search.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.web_search import WebSearchTool


class TestWebSearchTool:
    """WebSearchTool should shell out to curl and parse Brave API response."""

    @pytest.fixture
    def tool(self) -> WebSearchTool:
        return WebSearchTool(api_key="test-key")

    def test_tool_metadata(self, tool: WebSearchTool) -> None:
        assert tool.name == "web_search"
        assert "query" in tool.input_schema["properties"]
        assert tool.input_schema["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self, tool: WebSearchTool) -> None:
        api_response = {
            "web": {
                "results": [
                    {
                        "title": "Python Docs",
                        "url": "https://docs.python.org",
                        "description": "Official Python documentation",
                    },
                    {
                        "title": "Real Python",
                        "url": "https://realpython.com",
                        "description": "Tutorials and guides",
                    },
                ]
            }
        }
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(api_response).encode(), b""))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(query="python docs")

        assert "Python Docs" in result
        assert "https://docs.python.org" in result
        assert "Real Python" in result

    @pytest.mark.asyncio
    async def test_no_results(self, tool: WebSearchTool) -> None:
        api_response = {"web": {"results": []}}
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(api_response).encode(), b""))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(query="xyznonexistent")

        assert result == "No results found."

    @pytest.mark.asyncio
    async def test_curl_failure(self, tool: WebSearchTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"connection refused"))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(query="test")

        assert "Search failed" in result
        assert "connection refused" in result

    @pytest.mark.asyncio
    async def test_invalid_json_response(self, tool: WebSearchTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"not json", b""))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(query="test")

        assert "Failed to parse" in result

    @pytest.mark.asyncio
    async def test_count_clamped_to_10(self, tool: WebSearchTool) -> None:
        api_response = {"web": {"results": []}}
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(api_response).encode(), b""))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await tool.execute(query="test", count=50)
            # URL in the call should have count=10 (clamped)
            call_args = mock_exec.call_args[0]
            url_arg = [a for a in call_args if "count=" in str(a)]
            assert any("count=10" in str(a) for a in url_arg)

    @pytest.mark.asyncio
    async def test_api_key_in_header(self, tool: WebSearchTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps({"web": {"results": []}}).encode(), b""))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await tool.execute(query="test")
            call_args = mock_exec.call_args[0]
            assert "X-Subscription-Token: test-key" in call_args

    @pytest.mark.asyncio
    async def test_query_url_encoded(self, tool: WebSearchTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps({"web": {"results": []}}).encode(), b""))

        with patch("src.tools.web_search.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await tool.execute(query="hello world")
            call_args = mock_exec.call_args[0]
            url_arg = [a for a in call_args if "q=" in str(a)][0]
            assert "hello+world" in url_arg
