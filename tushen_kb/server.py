"""Resident HTTP service (stdlib only), bound to 127.0.0.1 only.

POST /ingest  {"manifest": true} | {"path": "...", "meta": {...}}
POST /search  {"q": "...", "mode": "keyword", "k": 10}
POST /ask     {"q": "...", "k": 10}
GET  /health
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ask import AskConfig, ask
from .ingest import ROOT, ingest_file, ingest_manifest
from .dense import VectorsMissing
from .search import SearchConfig, search
from .store import Store

ALLOWED_HOSTS = ("127.0.0.1",)


def make_server(db_path: str, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to bind {host}: this service binds 127.0.0.1 only")
    store = Store(db_path)
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "docs": [d["doc_id"] for d in store.docs()]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._send(400, {"error": f"bad json: {e}"})
            try:
                with lock:
                    if self.path == "/ingest":
                        if req.get("path"):
                            p = Path(req["path"]).resolve()
                            if ROOT not in p.parents:
                                return self._send(400, {"error": "path must be inside the repo"})
                            return self._send(200, {"ingested": [ingest_file(store, p, req.get("meta"))]})
                        return self._send(200, {"ingested": ingest_manifest(store)})
                    if self.path == "/search":
                        cfg = SearchConfig(mode=req.get("mode", "keyword"), k=int(req.get("k", 10)),
                                           clause_route=bool(req.get("clause_route", True)))
                        hits = search(store, req["q"], cfg)
                        return self._send(200, {"q": req["q"], "mode": cfg.mode, "hits": hits})
                    if self.path == "/ask":
                        cfg = AskConfig(search=SearchConfig(k=int(req.get("k", 10))))
                        return self._send(200, ask(store, req["q"], cfg))
                return self._send(404, {"error": "not found"})
            except NotImplementedError as e:
                return self._send(501, {"error": str(e)})
            except VectorsMissing as e:
                return self._send(503, {"error": str(e)})
            except KeyError as e:
                return self._send(400, {"error": f"missing field {e}"})
            except Exception as e:  # pragma: no cover
                return self._send(500, {"error": repr(e)})

    srv = ThreadingHTTPServer((host, port), H)
    srv.store = store  # type: ignore[attr-defined]
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = make_server(a.db, port=a.port)
    print(f"tushen-kb listening on http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
