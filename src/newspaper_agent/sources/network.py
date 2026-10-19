"""Bounded public HTTPS access, with DNS answers pinned before TLS connects."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urljoin


class SourceNetworkError(ValueError):
    """A stable error code; never includes a URL, credential or response body."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if getattr(address, "ipv4_mapped", None) is not None:
            address = address.ipv4_mapped
        return (address.is_global and not address.is_multicast
                and not address.is_reserved and not address.is_unspecified)
    except ValueError:
        return False


def validate_https_url(url: str, *, allow_query: bool = True) -> str:
    """Syntax gate; DNS validation is performed again on every actual request."""
    try:
        if (not isinstance(url, str) or not 1 <= len(url) <= 4096
                or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or "\\" in url):
            raise ValueError
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username or parts.password
                or parts.port not in (None, 443) or parts.fragment
                or (parts.query and not allow_query)):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if ("." not in host or host.endswith((".local", ".internal", ".localhost"))
                    or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
                    or re.fullmatch(r"[0-9.]+", host)
                    or any(not label or len(label) > 63 or label.startswith("-")
                           or label.endswith("-") for label in host.split("."))):
                raise ValueError
        else:
            if not _public_ip(host):
                raise ValueError
    except (ValueError, TypeError):
        raise SourceNetworkError("unsafe_url") from None
    return url


def resolve_public_addresses(host: str) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
    except (OSError, ValueError):
        raise SourceNetworkError("dns_failed") from None
    # Reject mixed public/private answers, instead of trying the public subset.
    if not addresses or not all(_public_ip(address) for address in addresses):
        raise SourceNetworkError("unsafe_dns")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        # create_connection receives an already validated IP, not the hostname.
        sock = socket.create_connection((self._address, 443), timeout=self.timeout)
        try:
            peer = sock.getpeername()[0]
            if not _public_ip(peer) or ipaddress.ip_address(peer) != ipaddress.ip_address(self._address):
                raise SourceNetworkError("unsafe_peer")
            # SNI and certificate matching still use the original hostname.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


@dataclass(frozen=True)
class HTTPResponse:
    body: bytes
    status: int = 200
    content_type: str = ""
    final_url: str = ""

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeError):
            raise SourceNetworkError("invalid_json") from None


_RATE_LOCK = threading.Lock()
_LAST_REQUEST: dict[str, float] = {}


class SafeHTTPClient:
    """No ambient proxies/cookies/credentials; redirects opt in only for safe article reads."""

    def __init__(self, *, timeout: float = 12, max_bytes: int = 1_000_000):
        if not 1 <= timeout <= 30 or not 1024 <= max_bytes <= 2_000_000:
            raise ValueError("无效网络预算")
        self.timeout, self.max_bytes = timeout, max_bytes

    def get(self, url: str) -> HTTPResponse:
        return self._request("GET", url)

    def get_page(self, url: str) -> HTTPResponse:
        """Article pages only: each of at most three redirects is validated/pinned anew."""
        return self._request('GET', url, redirects=3)

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> HTTPResponse:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > 16_384:
            raise SourceNetworkError("request_too_large")
        return self._request("POST", url, body, headers)

    def _request(self, method, url, body=None, extra_headers=None, *, redirects=0):
        validate_https_url(url)
        parts = urlsplit(url)
        host = parts.hostname
        if extra_headers and (host != "api.tavily.com"
                              or set(extra_headers) != {"Authorization"}):
            raise SourceNetworkError("unsupported_headers")
        # Shared within this process, including separate adapter instances.
        interval = 3.1 if host in ("export.arxiv.org", "arxiv.org") else 0.4 if host == "eutils.ncbi.nlm.nih.gov" else 0.15
        with _RATE_LOCK:
            delay = interval - (time.monotonic() - _LAST_REQUEST.get(host, -1e9))
            if delay > 0:
                time.sleep(delay)
            _LAST_REQUEST[host] = time.monotonic()
        addresses = resolve_public_addresses(host)
        connection = _PinnedHTTPSConnection(host, addresses[0], self.timeout)
        headers = {"User-Agent": "newspaper-agent/0.1 (personal research client)",
                   "Accept": "application/json, application/atom+xml, application/rss+xml, application/xml, text/html;q=0.5",
                   "Accept-Encoding": "identity", "Connection": "close"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        started = time.monotonic()
        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                location = response.getheader('Location')
                if method == 'GET' and redirects and location:
                    target_url = urljoin(url, location)
                    validate_https_url(target_url)
                    connection.close()
                    return self._request('GET', target_url, redirects=redirects - 1)
                raise SourceNetworkError("redirect_blocked")
            if response.status == 429:
                raise SourceNetworkError("rate_limited")
            if response.status in (401, 403):
                raise SourceNetworkError("access_denied")
            if response.status != 200:
                raise SourceNetworkError("http_error")
            if response.getheader("Content-Encoding", "identity").lower() not in ("identity", ""):
                raise SourceNetworkError("unsupported_encoding")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
                raise SourceNetworkError("response_too_large")
            chunks, total = [], 0
            while True:
                remaining = self.timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise SourceNetworkError("request_timeout")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65_536, self.max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self.max_bytes:
                    raise SourceNetworkError("response_too_large")
            return HTTPResponse(b"".join(chunks), response.status, response.getheader("Content-Type", ""), url)
        except SourceNetworkError:
            raise
        except (TimeoutError, socket.timeout):
            raise SourceNetworkError("request_timeout") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise SourceNetworkError("request_failed") from None
        finally:
            connection.close()
