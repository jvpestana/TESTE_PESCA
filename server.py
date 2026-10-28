#!/usr/bin/env python3
"""App web do Dia de pesca. Uso: python3 server.py"""

from __future__ import annotations

import json
import re
import threading
import time
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import forecast

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SLUG = re.compile(r"^[a-z0-9-]{1,80}$")
# HOST = "0.0.0.0"
# PORT = 8787
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8787))
TTL = 600

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, loader):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def _fresh(key: str, loader):
    value = loader()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "DiaDePesca/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path == "/api/municipios":
                data = _cached("mun", forecast.list_municipios)
                self._json(200, data)
                return
            if path == "/api/praias":
                mun = (qs.get("municipio") or [""])[0]
                if not SLUG.match(mun):
                    self._json(400, {"error": "Cidade inválida."})
                    return
                data = _cached("praias:" + mun, lambda: forecast.list_praias(mun))
                self._json(200, data)
                return
            if path == "/api/previsao":
                mun = (qs.get("municipio") or [""])[0]
                praia = (qs.get("praia") or [""])[0]
                if not SLUG.match(mun) or not SLUG.match(praia):
                    self._json(400, {"error": "Escolha uma cidade e uma praia do Espírito Santo."})
                    return
                key = f"prev:{mun}/{praia}"
                fresh = (qs.get("fresh") or ["0"])[0] == "1"
                loader = lambda: forecast.load_beach(mun, praia)
                data = _fresh(key, loader) if fresh else _cached(key, loader)
                self._json(200, data)
                return
        except forecast.ForecastError as exc:
            self._json(502, {"error": str(exc)})
            return
        except Exception:
            import traceback
            traceback.print_exc()
            self._json(500, {"error": "Falha ao montar a previsão."})
            return

        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self._send(404, "Não encontrado".encode(), "text/plain; charset=utf-8")
            return
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".webmanifest": "application/manifest+json",
            ".svg": "image/svg+xml",
        }
        self._send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dia de pesca em http://127.0.0.1:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
