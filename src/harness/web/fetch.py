"""Direct HTTP fetching under the network policy (SEC3, SEC4).

* Pages are fetched straight from the origin; no hosted fetch or scraping service.
* Every redirect hop is checked against the network policy before it is followed.
* No cookies are ever sent, and none are kept between requests.
* Responses are capped in size.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from harness.config import WebConfig
from harness.security.net import BlockedAddress, NetPolicy
from harness.web.html_text import html_to_text

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    title: str = ""
    links: list[tuple[str, str]] | None = None
    truncated: bool = False

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type


class SafeFetcher:
    def __init__(
        self, policy: NetPolicy, config: WebConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.policy = policy
        self.config = config
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=self.config.fetch_timeout_s,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5",
            },
            transport=self._transport,
        )

    def fetch(self, url: str) -> FetchedPage:
        """Fetch ``url`` and convert HTML to text. Raises FetchError or BlockedAddress."""
        current = url
        with self._client() as client:
            for _hop in range(self.config.max_redirects + 1):
                self.policy.check_url(current)
                try:
                    with client.stream("GET", current) as response:
                        client.cookies.clear()
                        if 300 <= response.status_code < 400 and response.headers.get("location"):
                            current = str(response.url.join(response.headers["location"]))
                            log.info("redirect -> %s", current)
                            continue
                        if response.status_code >= 400:
                            raise FetchError(f"HTTP {response.status_code} fetching {current}")
                        content_type = response.headers.get("content-type", "").lower()
                        body, truncated = _read_capped(response, self.config.max_page_bytes)
                except httpx.HTTPError as exc:
                    raise FetchError(f"{exc.__class__.__name__} fetching {current}: {exc}") from exc
                encoding = response.charset_encoding or "utf-8"
                text = body.decode(encoding, errors="replace")
                if "html" in content_type or (not content_type and text.lstrip()[:1] == "<"):
                    extracted = html_to_text(text)
                    return FetchedPage(
                        url,
                        current,
                        response.status_code,
                        content_type or "text/html",
                        extracted.text,
                        extracted.title,
                        extracted.links,
                        truncated,
                    )
                return FetchedPage(
                    url, current, response.status_code, content_type, text, "", [], truncated
                )
        raise FetchError(
            f"too many redirects (more than {self.config.max_redirects}) fetching {url}"
        )

    def download(
        self, url: str, destination: Path, max_bytes: int, cancel=None
    ) -> tuple[str, int, str]:
        """Stream ``url`` into ``destination`` (never through memory or the model).

        Same checks as fetch: the network policy on every redirect hop, no cookies. Written to
        ``<destination>.part`` and renamed only when complete; over ``max_bytes`` it stops and
        deletes the partial file. Returns (final URL, size, content type).
        """
        current = url
        partial = destination.with_name(destination.name + ".part")
        with self._client() as client:
            for _hop in range(self.config.max_redirects + 1):
                self.policy.check_url(current)
                try:
                    with client.stream("GET", current, headers={"Accept": "*/*"}) as response:
                        client.cookies.clear()
                        if 300 <= response.status_code < 400 and response.headers.get("location"):
                            current = str(response.url.join(response.headers["location"]))
                            log.info("redirect -> %s", current)
                            continue
                        if response.status_code >= 400:
                            raise FetchError(f"HTTP {response.status_code} fetching {current}")
                        size = 0
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with open(partial, "wb") as out:
                            for chunk in response.iter_bytes():
                                if cancel is not None and cancel.cancelled:
                                    raise FetchError("download cancelled")
                                size += len(chunk)
                                if size > max_bytes:
                                    raise FetchError(
                                        f"{current} is larger than the download limit "
                                        f"({max_bytes // 2**20} MB, web.max_download_mb)"
                                    )
                                out.write(chunk)
                        partial.replace(destination)
                        return current, size, response.headers.get("content-type", "")
                except httpx.HTTPError as exc:
                    partial.unlink(missing_ok=True)
                    raise FetchError(f"{exc.__class__.__name__} fetching {current}: {exc}") from exc
                except FetchError:
                    partial.unlink(missing_ok=True)
                    raise
        raise FetchError(
            f"too many redirects (more than {self.config.max_redirects}) fetching {url}"
        )


def _read_capped(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            return b"".join(chunks)[:limit], True
    return b"".join(chunks), False


__all__ = ["BlockedAddress", "FetchError", "FetchedPage", "SafeFetcher"]
