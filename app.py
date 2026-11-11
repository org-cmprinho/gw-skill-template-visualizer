#!/usr/bin/env python3
"""GW Skill Template Visualizer – tiny local server + browser UI.

    python3 app.py                # serves on http://127.0.0.1:8765 and opens the browser
    python3 app.py --port 9000 --no-browser
    python3 app.py --data /path/to/Templates/Skills

The template tree is read live from the data folder on every reload, so new .txt
templates show up without restarting.
"""
import argparse
import http.server
import json
import re
import threading
import webbrowser
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CODE_RE = re.compile(r"[A-Za-z0-9+/]{8,}")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read_notes(data_dir):
    """Parse BUILD_INDEX.md-style files: '## channel' / '### build' headings → note text."""
    notes = {}
    for md in data_dir.rglob("*.md"):
        channel, title, buf = None, None, []

        def flush():
            if channel and title and buf:
                text = "\n".join(buf).strip().strip("`").strip()
                if text:
                    notes[norm(channel) + "/" + norm(title)] = text

        for line in md.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("## "):
                flush(); channel, title, buf = line[3:].strip(), None, []
            elif line.startswith("### "):
                flush(); title, buf = line[4:].strip(), []
            elif title is not None:
                buf.append(line)
        flush()
    return notes


def scan(data_dir):
    """All .txt skill templates under data_dir, grouped by folder."""
    folders = {}
    for f in sorted(data_dir.rglob("*.txt"), key=lambda p: [s.lower() for s in p.parts]):
        try:
            raw = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        m = CODE_RE.search(raw)
        if not m:
            continue
        rel = f.parent.relative_to(data_dir).as_posix()
        folders.setdefault(rel, []).append({"name": f.stem, "code": m.group(0)})
    notes = read_notes(data_dir)
    out = []
    for path, files in folders.items():
        parts = path.split("/")
        note = notes.get(norm(parts[-2]) + "/" + norm(parts[-1])) if len(parts) >= 2 else None
        out.append({"path": path, "files": files, "note": note})
    return {"root": str(data_dir), "folders": out}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, data_dir, **kw):
        self.data_dir = data_dir
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/web/index.html"
        if self.path.split("?")[0] == "/api/templates":
            body = json.dumps(scan(self.data_dir), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.path.startswith(("/web/", "/resources/")):
            self.send_error(404)
            return
        super().do_GET()

    def end_headers(self):
        if self.path.startswith("/resources/icons/") or self.path.startswith("/resources/ui/"):
            self.send_header("Cache-Control", "max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not (ROOT / "resources" / "skills.json").exists():
        raise SystemExit("resources/skills.json missing – run tools/fetch_wikis.py and tools/build_resources.py")
    handler = partial(Handler, data_dir=args.data.resolve())
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"GW Skill Template Visualizer → {url}  (data: {args.data.resolve()})  Ctrl+C to quit")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
