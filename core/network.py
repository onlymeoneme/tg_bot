"""Сетевой слой: HTTP-запросы (опциональный Fake-SNI туннель)."""

import ssl
import socket
import http.client
import urllib.request
import urllib.error
import json
import logging

from config import USE_FAKE_SNI, FAKE_SNI, REQUEST_TIMEOUT

log = logging.getLogger(__name__)


class _FakeSNIHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS-соединение с подменённым SNI (обход DPI)."""

    def __init__(self, host: str, fake_sni: str | None = None, **kwargs):
        super().__init__(host, **kwargs)
        self.fake_sni = fake_sni

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), self.timeout)
        ctx  = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(sock, server_hostname=self.fake_sni or self.host)


class _FakeSNIHandler(urllib.request.HTTPSHandler):
    def __init__(self, fake_sni: str):
        super().__init__()
        self.fake_sni = fake_sni

    def https_open(self, req: urllib.request.Request):
        def _make_conn(host, **kwargs):
            return _FakeSNIHTTPSConnection(host, fake_sni=self.fake_sni, **kwargs)
        return self.do_open(_make_conn, req)


def make_opener() -> urllib.request.OpenerDirector:
    if USE_FAKE_SNI:
        return urllib.request.build_opener(_FakeSNIHandler(FAKE_SNI))
    return urllib.request.build_opener()


def do_request(
    method: str,
    url: str,
    headers: dict | None = None,
    data: dict | None = None,
) -> tuple[int, bytes]:
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(url, data=body, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    opener = make_opener()
    log.debug("%s %s", method, url)
    with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.status, resp.read()
