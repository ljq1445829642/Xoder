#!/usr/bin/env python3
"""Xoder Repo Wiki Dashboard Server.

Serves the dashboard SPA and wiki content from .xoder/repowiki/zh/content/.
Supports CORS for local development. No dependencies beyond stdlib.
"""

import http.server
import json
import os
import re
import urllib.parse
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8920

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DASHBOARD_DIR = SCRIPT_DIR
WIKI_DIR = PROJECT_DIR / ".xoder" / "repowiki" / "zh" / "content"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


def _md_title(filepath: Path) -> str:
    """Extract title from the first # heading in a markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8")
        m = re.match(r"^#\s+(.+)", text)
        return m.group(1).strip() if m else filepath.stem
    except Exception:
        return filepath.stem


def _determine_status(name: str) -> str:
    """Heuristic: check if the markdown file has substantive content."""
    mapping = {
        "overview": "success",
        "start": "success",
        "architecture": "pending",
        "api_spec": "pending",
        "db_schema": "pending",
    }
    return mapping.get(name, "pending")


def build_page_list() -> list[dict]:
    """Walk WIKI_DIR and return a list of page descriptors."""
    pages: list[dict] = []

    if not WIKI_DIR.exists():
        return pages

    # Top-level .md files first
    for entry in sorted(WIKI_DIR.glob("*.md")):
        pid = entry.stem
        pages.append({
            "id": pid,
            "name": _md_title(entry),
            "path": str(entry.relative_to(PROJECT_DIR)).replace("\\", "/"),
            "status": _determine_status(pid),
            "type": "core",
        })

    # Subdirectories (modules/*)
    for subdir in sorted(WIKI_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        for entry in sorted(subdir.glob("*.md")):
            pid = f"{subdir.name}/{entry.stem}"
            pages.append({
                "id": pid,
                "name": _md_title(entry),
                "path": str(entry.relative_to(PROJECT_DIR)).replace("\\", "/"),
                "status": "pending",
                "type": "module",
            })

    return pages


def search_wiki(query: str) -> list[dict]:
    """Full-text search across all .md files in WIKI_DIR."""
    results = []
    if not WIKI_DIR.exists():
        return results

    q = query.lower()
    for md_file in WIKI_DIR.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            if q in text.lower():
                idx = text.lower().index(q)
                start = max(0, idx - 60)
                end = min(len(text), idx + len(q) + 120)
                snippet = text[start:end].strip()
                pid = str(md_file.relative_to(WIKI_DIR)).replace("\\", "/").replace(".md", "")
                results.append({
                    "page": pid,
                    "title": _md_title(md_file),
                    "snippet": f"...{snippet}..." if start > 0 else snippet,
                })
        except Exception:
            continue
    return results[:20]


class WikiHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def send_cors_headers(self):
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text_response(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # ── API routes ──────────────────────────────────────────
        if path == "/api/pages":
            pages = build_page_list()
            self._json_response(pages)
            return

        if path.startswith("/api/page/"):
            page_id = path[len("/api/page/"):]
            # Normalise: strip leading/trailing slashes
            page_id = page_id.strip("/")
            md_rel = page_id + ".md"
            md_path = WIKI_DIR / md_rel

            if md_path.exists():
                try:
                    content = md_path.read_text(encoding="utf-8")
                    self._text_response(content)
                except Exception:
                    self._json_response({"error": "Read error"}, 500)
                return
            self._json_response({"error": "Not found"}, 404)
            return

        if path == "/api/search":
            query = qs.get("q", [""])[0]
            if query:
                results = search_wiki(query)
                self._json_response(results)
            else:
                self._json_response([])
            return

        # ── Serve .xoder/repowiki files (raw) ───────────────────
        wiki_prefix = "/.xoder/"
        if path.startswith(wiki_prefix):
            rel = path[len(wiki_prefix):]
            fs_path = PROJECT_DIR / ".xoder" / rel
            if fs_path.exists() and fs_path.is_file():
                content = fs_path.read_bytes()
                self.send_response(200)
                self.send_cors_headers()
                ct = "image/svg+xml" if fs_path.suffix == ".svg" else "text/plain; charset=utf-8"
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self._json_response({"error": "Not found"}, 404)
            return

        # ── Default: serve static files from dashboard/ ─────────
        super().do_GET()


def main():
    server = http.server.HTTPServer((HOST, PORT), WikiHTTPHandler)
    server_addr = f"http://{HOST}:{PORT}"
    print(f"\n  Xoder Repo Wiki Dashboard")
    print(f"  {'─' * 28}")
    print(f"  Server:   {server_addr}")
    print(f"  Wiki dir: {WIKI_DIR}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
