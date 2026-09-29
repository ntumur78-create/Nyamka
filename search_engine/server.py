"""Хайлтын системийн HTTP API ба веб интерфэйс (зөвхөн стандарт сан ашиглана)."""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .index import SearchIndex

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_LIMIT = 100
FACET_FIELDS = ["category", "tags"]


def make_handler(index: SearchIndex, data_path: Optional[str] = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NyamkaSearch/1.0"

        def log_message(self, fmt, *args):  # noqa: D401 - чимээгүй лог
            if os.environ.get("SEARCH_LOG"):
                super().log_message(fmt, *args)

        # -------------------------------------------------------------
        def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status)

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise ValueError("Хүсэлт хэт том байна")
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw.decode("utf-8") or "null")

        def _persist(self) -> None:
            if data_path:
                index.save(data_path)

        # -------------------------------------------------------------
        def do_GET(self):
            url = urlparse(self.path)
            params = {k: v[-1] for k, v in parse_qs(url.query).items()}
            path = url.path.rstrip("/") or "/"

            if path in ("/", "/index.html"):
                return self._serve_static("index.html")
            if path == "/api/search":
                try:
                    limit = min(int(params.get("limit", 10)), MAX_LIMIT)
                    offset = int(params.get("offset", 0))
                except ValueError:
                    return self._error(HTTPStatus.BAD_REQUEST, "limit/offset тоо байх ёстой")
                filters = {k: params[k] for k in FACET_FIELDS if params.get(k)}
                result = index.search(
                    params.get("q", ""),
                    limit=limit,
                    offset=offset,
                    filters=filters,
                    facets=FACET_FIELDS,
                    fuzzy=params.get("fuzzy", "1") != "0",
                    prefix=params.get("prefix", "1") != "0",
                )
                return self._send_json(result)
            if path == "/api/autocomplete":
                return self._send_json({"suggestions": index.autocomplete(params.get("q", ""))})
            if path == "/api/stats":
                return self._send_json(index.stats())
            if path == "/api/documents":
                return self._send_json({"documents": index.documents()})
            if path.startswith("/api/documents/"):
                doc = index.get(unquote(path[len("/api/documents/") :]))
                if doc is None:
                    return self._error(HTTPStatus.NOT_FOUND, "Баримт олдсонгүй")
                return self._send_json(doc)
            return self._error(HTTPStatus.NOT_FOUND, "Хуудас олдсонгүй")

        def do_POST(self):
            if urlparse(self.path).path.rstrip("/") != "/api/documents":
                return self._error(HTTPStatus.NOT_FOUND, "Хуудас олдсонгүй")
            try:
                payload = self._read_json()
            except (ValueError, UnicodeDecodeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, f"JSON буруу байна: {exc}")
            docs = payload if isinstance(payload, list) else [payload]
            if not docs or not all(isinstance(d, dict) for d in docs):
                return self._error(HTTPStatus.BAD_REQUEST, "Баримт нь JSON объект байх ёстой")
            ids = index.add_many(docs)
            self._persist()
            return self._send_json({"ids": ids}, HTTPStatus.CREATED)

        def do_DELETE(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/documents/"):
                return self._error(HTTPStatus.NOT_FOUND, "Хуудас олдсонгүй")
            if not index.remove(unquote(path[len("/api/documents/") :])):
                return self._error(HTTPStatus.NOT_FOUND, "Баримт олдсонгүй")
            self._persist()
            return self._send_json({"deleted": True})

        def _serve_static(self, name: str) -> None:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                body = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def create_server(index: SearchIndex, host: str = "127.0.0.1", port: int = 8000, data_path: Optional[str] = None):
    return ThreadingHTTPServer((host, port), make_handler(index, data_path))
