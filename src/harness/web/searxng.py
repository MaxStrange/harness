"""SearXNG client (S12, SEC3): the only search backend, no fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


class SearchError(Exception):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str = ""


class SearxClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 30,
        user_agent: str = "ai-harness",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.user_agent = user_agent
        self._transport = transport

    def search(
        self,
        query: str,
        *,
        count: int = 10,
        categories: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        params = {"q": query, "format": "json"}
        if categories:
            params["categories"] = categories
        if language:
            params["language"] = language
        try:
            with httpx.Client(
                trust_env=False,
                timeout=self.timeout_s,
                transport=self._transport,
                headers={"User-Agent": self.user_agent},
            ) as client:
                response = client.get(f"{self.base_url}/search", params=params)
        except httpx.HTTPError as exc:
            raise SearchError(
                f"SearXNG at {self.base_url} is unreachable: {exc.__class__.__name__}: {exc}. Search is down until it is back."
            ) from exc
        if response.status_code >= 400:
            raise SearchError(
                f"SearXNG at {self.base_url} returned HTTP {response.status_code}. (Is the JSON format enabled in its settings?)"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchError(f"SearXNG returned non-JSON output: {exc}") from exc
        results = []
        for item in data.get("results", [])[:count]:
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(url),
                    snippet=str(item.get("content", "")),
                    engine=str(item.get("engine", "")),
                )
            )
        return results
