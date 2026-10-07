"""The Browser Assistant's network choke point (V0.13 SSRF hardening).

When private hosts are not allowed (every real deployment), the assistant's Chromium sends
ALL of its traffic -- navigations, redirects, images, scripts, fetch/XHR, WebSockets --
through this in-process forward proxy, and the proxy decides where a connection may go:

- it resolves the destination itself and refuses it when the name is an intranet name or
  ANY of its addresses is loopback, private, link-local (cloud metadata, 169.254.169.254),
  multicast, reserved or unspecified;
- it then connects to the very address it checked, so a DNS answer that changes between
  the check and the connection (DNS rebinding) cannot reach an internal address;
- Chromium is started without its implicit "never proxy localhost" bypass and without
  non-proxied WebRTC UDP, so nothing goes around it.

The page-level guard in playwright_driver.py stays as a first layer, but it only sees the
first request of a navigation (Playwright does not route redirect hops) and only
navigations; this proxy sees everything.

HTTPS is tunnelled (CONNECT) and never decrypted: the proxy sees host and port only.
Plain-HTTP requests are forwarded with `Connection: close`, one request per upstream
connection, so a reused client connection can never reach a second, unchecked host.
"""

import asyncio
import contextlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.services.automation import safety

logger = get_logger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]
AddressPolicy = Callable[[str, str], bool]

_HEAD_LIMIT = 64 * 1024
_HEAD_TIMEOUT_SECONDS = 30.0
_CONNECT_TIMEOUT_SECONDS = 15.0
_RESOLVE_TIMEOUT_SECONDS = 5.0
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailer",
}

#: Chromium switches that force every connection through the proxy.
CHROMIUM_PROXY_ARGS = (
    # Chromium never proxies loopback by default; "<-loopback>" removes that exception.
    "--proxy-bypass-list=<-loopback>",
    # WebRTC may otherwise open direct UDP connections (STUN) around the proxy.
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    # HTTP/3 runs over UDP, which an HTTP proxy does not carry: never use it.
    "--disable-quic",
)


async def _system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), _RESOLVE_TIMEOUT_SECONDS
    )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def public_address_only(host: str, address: str) -> bool:
    """The default policy: a destination is allowed only on a public address."""
    try:
        return not safety._address_is_private(ipaddress.ip_address(address))  # noqa: SLF001
    except ValueError:
        return False


class EgressFilterProxy:
    def __init__(
        self,
        *,
        resolve: Resolver | None = None,
        address_allowed: AddressPolicy | None = None,
    ) -> None:
        self._resolve = resolve or _system_resolve
        self._allowed = address_allowed or public_address_only
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.port = 0
        #: Destinations refused so far (host:port), for tests and diagnostics.
        self.blocked: list[str] = []

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def chromium_args(self) -> list[str]:
        return [f"--proxy-server={self.server_url}", *CHROMIUM_PROXY_ARGS]

    async def start(self) -> "EgressFilterProxy":
        self._server = await asyncio.start_server(
            self._accept, host="127.0.0.1", port=0, limit=_HEAD_LIMIT
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        for task in list(self._tasks):
            task.cancel()

    # --- connection handling -----------------------------------------------------------------

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._handle(reader, writer)
        except (asyncio.CancelledError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:  # noqa: BLE001 -- one bad connection never stops the proxy
            logger.warning("event=browser_egress_proxy_error")
        finally:
            writer.close()
            if task is not None:
                self._tasks.discard(task)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT_SECONDS)
        except (asyncio.LimitOverrunError, TimeoutError, asyncio.IncompleteReadError):
            return
        request_line, *header_lines = head.decode("latin-1").rstrip("\r\n").split("\r\n")
        try:
            method, target, version = request_line.split(" ", 2)
        except ValueError:
            await self._reply(writer, 400, "Bad Request")
            return

        if method.upper() == "CONNECT":
            host, port = _split_host_port(target, default_port=443)
            address = await self._checked(host, port)
            if address is None:
                await self._reply(writer, 403, "Forbidden")
                return
            upstream = await self._open(address, port)
            if upstream is None:
                await self._reply(writer, 502, "Bad Gateway")
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await _relay(reader, writer, *upstream)
            return

        url = urlsplit(target)
        if url.scheme != "http" or not url.hostname:
            await self._reply(writer, 400, "Bad Request")
            return
        port = url.port or 80
        address = await self._checked(url.hostname, port)
        if address is None:
            await self._reply(writer, 403, "Forbidden")
            return
        upstream = await self._open(address, port)
        if upstream is None:
            await self._reply(writer, 502, "Bad Gateway")
            return
        path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        headers = [
            line
            for line in header_lines
            if line and line.split(":", 1)[0].strip().lower() not in _HOP_BY_HOP
        ]
        upgrade = any(line.split(":", 1)[0].strip().lower() == "upgrade" for line in headers)
        connection = "Connection: upgrade" if upgrade else "Connection: close"
        upstream_head = "\r\n".join([f"{method} {path} {version}", *headers, connection, "", ""])
        upstream_reader, upstream_writer = upstream
        upstream_writer.write(upstream_head.encode("latin-1"))
        await upstream_writer.drain()
        await _relay(reader, writer, upstream_reader, upstream_writer)

    async def _checked(self, host: str, port: int) -> str | None:
        """The address to connect to, or None when the destination is not allowed."""
        name = host.strip("[]").lower().rstrip(".")
        if not name:
            self._refuse(host, port)
            return None
        literal = safety._as_ip(name)  # noqa: SLF001
        if literal is None and safety.host_is_private(name):
            self._refuse(host, port)
            return None
        try:
            addresses = [str(literal)] if literal is not None else await self._resolve(name, port)
        except (OSError, TimeoutError):
            addresses = []
        if not addresses or not all(self._allowed(name, address) for address in addresses):
            self._refuse(host, port)
            return None
        return addresses[0]

    def _refuse(self, host: str, port: int) -> None:
        self.blocked.append(f"{host}:{port}")
        logger.warning("event=browser_egress_blocked host=%s port=%d", host, port)
        return None

    async def _open(
        self, address: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port), _CONNECT_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError):
            return None

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        with contextlib.suppress(ConnectionError):
            await writer.drain()


def _split_host_port(target: str, *, default_port: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = target.rpartition(":") if target.count(":") == 1 else (target, "", "")
    try:
        return host, int(port) if port else default_port
    except ValueError:
        return host, default_port


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while data := await source.read(65536):
            sink.write(data)
            await sink.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    """Copy both directions until either side closes, then close both."""
    tasks = {
        asyncio.ensure_future(_pipe(client_reader, upstream_writer)),
        asyncio.ensure_future(_pipe(upstream_reader, client_writer)),
    }
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        upstream_writer.close()
        client_writer.close()
