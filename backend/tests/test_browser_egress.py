"""SSRF regression tests for the Browser Assistant's network egress (V0.13).

V0.10's page-level guard aborted *navigations* to private hosts, but a public page could
still (1) redirect the browser to an internal address -- Playwright does not route redirect
hops -- and (2) make it load internal URLs as images / fetch() / XHR. V0.13 sends every
connection through `EgressFilterProxy`. These tests reproduce both attacks with a real
headless Chromium: "public.example" is a public-looking name that the proxy's (injected)
resolver maps to the local test server, while the internal target is that same server
addressed as 127.0.0.1.
"""

import asyncio
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.services.automation.browser.base import BrowserError
from app.services.automation.browser.egress_proxy import EgressFilterProxy
from app.services.automation.browser.playwright_driver import PlaywrightDriver

PUBLIC = "public.example"


class _Site:
    def __init__(self) -> None:
        self.hits: list[str] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                site.hits.append(self.path)
                port = site.port
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{port}/secret")
                    self.end_headers()
                    return
                body = b"secret internal page"
                if self.path == "/page":
                    body = (
                        f'<html><body><h1>Apply</h1><input name="email">'
                        f'<img src="http://127.0.0.1:{port}/internal-img">'
                        f'<img src="http://169.254.169.254/latest/meta-data/">'
                        f'<script>fetch("http://127.0.0.1:{port}/internal-fetch",'
                        f'{{mode:"no-cors"}}).catch(()=>{{}})</script></body></html>'
                    ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture
def site() -> Generator[_Site, None, None]:
    server = _Site()
    yield server
    server.stop()


async def _fake_resolve(host: str, port: int) -> list[str]:
    return {PUBLIC: ["127.0.0.1"]}.get(host, [])


def _only_public_example(host: str, address: str) -> bool:
    # The test's stand-in for "a public address": only the public-looking name is allowed.
    return host == PUBLIC


@pytest.fixture
def proxies() -> list[EgressFilterProxy]:
    return []


@pytest.fixture
def browser(proxies: list[EgressFilterProxy]):  # type: ignore[no-untyped-def]
    def factory() -> EgressFilterProxy:
        proxy = EgressFilterProxy(resolve=_fake_resolve, address_allowed=_only_public_example)
        proxies.append(proxy)
        return proxy

    session = PlaywrightDriver(headless=True, egress_proxy_factory=factory).launch(
        allow_private_hosts=False
    )
    yield session
    session.close()


def test_a_public_page_loads_through_the_proxy_but_its_internal_requests_do_not(
    browser: Any, site: _Site, proxies: list[EgressFilterProxy]
) -> None:
    browser.open(f"http://{PUBLIC}:{site.port}/page", timeout_seconds=20)
    snapshot = browser.page.snapshot(max_controls=50)
    assert "email" in {c.name for c in snapshot.controls}  # the page works normally
    assert "/page" in site.hits
    # The page's image and fetch() to internal addresses never reached them.
    assert "/internal-img" not in site.hits
    assert "/internal-fetch" not in site.hits
    blocked = proxies[0].blocked
    assert f"127.0.0.1:{site.port}" in blocked
    assert "169.254.169.254:80" in blocked


def test_a_redirect_to_an_internal_address_is_not_followed(
    browser: Any, site: _Site, proxies: list[EgressFilterProxy]
) -> None:
    with pytest.raises(BrowserError):
        browser.open(f"http://{PUBLIC}:{site.port}/redirect", timeout_seconds=20)
    assert "/redirect" in site.hits
    assert "/secret" not in site.hits
    assert f"127.0.0.1:{site.port}" in proxies[0].blocked


def test_a_direct_internal_address_is_refused(browser: Any, site: _Site) -> None:
    with pytest.raises(BrowserError):
        browser.open(f"http://127.0.0.1:{site.port}/secret", timeout_seconds=20)
    assert "/secret" not in site.hits


# --- The proxy's decisions, at the socket level (no browser) -----------------------------------


async def _connect(proxy: EgressFilterProxy, target: str) -> tuple[bytes, Any, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()
    status = await reader.readuntil(b"\r\n\r\n")
    return status, reader, writer


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_connect_tunnels_to_an_allowed_host_only(site: _Site) -> None:
    async def scenario() -> tuple[bytes, bytes, bytes]:
        proxy = await EgressFilterProxy(
            resolve=_fake_resolve, address_allowed=_only_public_example
        ).start()
        try:
            ok, reader, writer = await _connect(proxy, f"{PUBLIC}:{site.port}")
            writer.write(b"GET /tunnelled HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            refused, _, writer2 = await _connect(proxy, f"127.0.0.1:{site.port}")
            writer2.close()
            return ok, response, refused
        finally:
            await proxy.close()

    ok, response, refused = _run(scenario())
    assert ok.startswith(b"HTTP/1.1 200")
    assert b"secret internal page" in response and "/tunnelled" in site.hits
    assert refused.startswith(b"HTTP/1.1 403")


@pytest.mark.parametrize(
    ("target", "answers"),
    [
        ("127.0.0.1:80", None),
        ("169.254.169.254:80", None),  # cloud metadata
        ("[::1]:443", None),
        ("10.1.2.3:443", None),
        ("intranet:443", None),  # single-label name
        ("db.internal:5432", None),
        ("rebind.example:443", ["10.0.0.5"]),  # public name, private answer
        ("mixed.example:443", ["93.184.216.34", "127.0.0.1"]),  # any private answer refuses
        ("nowhere.example:443", []),  # unresolvable
    ],
)
def test_the_default_policy_refuses_internal_destinations(
    target: str, answers: list[str] | None
) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return answers or []

    async def scenario() -> bytes:
        proxy = await EgressFilterProxy(resolve=resolve).start()
        try:
            status, _, writer = await _connect(proxy, target)
            writer.close()
            return status
        finally:
            await proxy.close()

    assert _run(scenario()).startswith(b"HTTP/1.1 403")


def test_plain_http_is_forwarded_for_one_request_with_connection_close(site: _Site) -> None:
    async def scenario() -> bytes:
        proxy = await EgressFilterProxy(
            resolve=_fake_resolve, address_allowed=_only_public_example
        ).start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(
                f"GET http://{PUBLIC}:{site.port}/plain HTTP/1.1\r\nHost: {PUBLIC}\r\n"
                "Proxy-Connection: keep-alive\r\n\r\n".encode()
            )
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            return data
        finally:
            await proxy.close()

    response = _run(scenario())
    assert response.startswith(b"HTTP/1.0 200") or response.startswith(b"HTTP/1.1 200")
    assert "/plain" in site.hits
