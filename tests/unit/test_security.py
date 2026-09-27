from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from harness.config import Config
from harness.security.net import BlockedAddress, NetPolicy, is_private_ip
from harness.security.paths import PathPolicy, resolve_path
from harness.web.fetch import FetchError, SafeFetcher
from harness.web.html_text import html_to_text
from harness.web.searxng import SearchError, SearxClient


def test_private_ip_detection():
    for ip in [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.5",
        "172.16.4.4",
        "169.254.1.1",
        "::1",
        "fe80::1",
        "100.64.0.1",
        "0.0.0.0",
        "::ffff:10.0.0.1",
        "garbage",
    ]:
        assert is_private_ip(ip), ip
    for ip in ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]:
        assert not is_private_ip(ip), ip


def make_policy(allowed=(), table=None):
    table = table or {}

    def resolver(host):
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return NetPolicy.from_config(list(allowed), "http://10.0.0.228:18082", resolver)


def test_public_host_allowed_private_blocked():
    policy = make_policy(
        table={"example.com": ["93.184.216.34"], "evil.com": ["93.184.216.34", "10.0.0.5"]}
    )
    assert policy.check_url("https://example.com/page") == "example.com"
    with pytest.raises(BlockedAddress):
        policy.check_url("https://evil.com/")
    with pytest.raises(BlockedAddress):
        policy.check_url("http://192.168.1.1/admin")
    with pytest.raises(BlockedAddress):
        policy.check_url("http://localhost:8080/")
    with pytest.raises(BlockedAddress):
        policy.check_url("http://printer.local/")
    with pytest.raises(BlockedAddress):
        policy.check_url("ftp://example.com/")
    with pytest.raises(BlockedAddress):
        policy.check_url("http://user:pw@example.com/")
    with pytest.raises(BlockedAddress):
        policy.check_url("http://nonexistent.invalid/")


def test_searxng_host_and_explicit_hosts_allowed():
    policy = make_policy(allowed=["nas"], table={})
    assert policy.check_url("http://10.0.0.228:18082/search") == "10.0.0.228"
    assert policy.check_url("http://nas/") == "nas"
    with pytest.raises(BlockedAddress):
        policy.check_url("http://10.0.0.229/")  # a neighbour is not implied


def test_path_policy(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("k")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    cfg = Config()
    cfg.security.deny_paths = [str(ssh), str(tmp_path / "does-not-exist")]
    cfg.web.untrusted_dirs = [str(downloads)]
    policy = PathPolicy.from_config(cfg.security, cfg.web)
    assert len(policy.deny_paths) == 1  # missing entries ignored without error
    assert policy.denial_reason(ssh / "id_rsa")
    assert policy.denial_reason(tmp_path / "proj" / ".env")
    assert policy.denial_reason(tmp_path / "proj" / "main.py") is None
    assert policy.is_untrusted(downloads / "repo" / "README.md")
    assert not policy.is_untrusted(tmp_path / "proj" / "README.md")


def test_resolve_path_relative_to_cwd(tmp_path):
    assert resolve_path("sub/x.txt", tmp_path) == (tmp_path / "sub" / "x.txt").resolve()
    assert resolve_path("~", tmp_path) == Path.home().resolve()


def test_html_to_text():
    html = "<html><head><title>T &amp; U</title><style>x{}</style></head><body><h1>Hi</h1><script>bad()</script><p>Para <a href='/x'>link</a></p><ul><li>one</li><li>two</li></ul></body></html>"
    out = html_to_text(html)
    assert out.title == "T & U"
    assert "bad()" not in out.text
    assert "Hi" in out.text and "- one" in out.text and "- two" in out.text
    assert out.links == [("link", "/x")]


def fetcher_with(handler, table=None, **cfg_overrides):
    cfg = Config().web
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    policy = make_policy(
        table=table or {"example.com": ["93.184.216.34"], "cdn.example.com": ["93.184.216.35"]}
    )
    return SafeFetcher(policy, cfg, transport=httpx.MockTransport(handler))


def test_fetch_follows_checked_redirects_without_cookies():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "example.com":
            return httpx.Response(
                302, headers={"location": "https://cdn.example.com/page", "set-cookie": "sid=1"}
            )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Page</title><p>Body</p>"
        )

    page = fetcher_with(handler).fetch("https://example.com/")
    assert page.final_url == "https://cdn.example.com/page"
    assert page.title == "Page" and "Body" in page.text
    assert "cookie" not in {k.lower() for k in seen[1].headers}


def test_fetch_refuses_redirect_to_private():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://10.0.0.1/secret"})

    with pytest.raises(BlockedAddress):
        fetcher_with(handler).fetch("https://example.com/")


def test_fetch_caps_size_and_reports_http_errors():
    def handler(request):
        if request.url.path == "/big":
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 1000)
        return httpx.Response(404)

    fetcher = fetcher_with(handler, max_page_bytes=100)
    page = fetcher.fetch("https://example.com/big")
    assert page.truncated and len(page.text) == 100
    with pytest.raises(FetchError):
        fetcher.fetch("https://example.com/missing")


def test_searxng_client_parses_and_reports_down():
    def handler(request):
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "A", "url": "https://a", "content": "snippet", "engine": "ddg"},
                    {"title": "no url"},
                ]
            },
        )

    client = SearxClient("http://10.0.0.228:18082", transport=httpx.MockTransport(handler))
    results = client.search("query")
    assert len(results) == 1 and results[0].url == "https://a"

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(SearchError) as info:
        SearxClient("http://10.0.0.228:18082", transport=httpx.MockTransport(down)).search("q")
    assert "unreachable" in str(info.value)
