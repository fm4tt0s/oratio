"""
server_http.py — Lightweight HTTP server exposing the RAG engine to the VS Code extension.

Endpoints:
  POST /check   { "text": "..." }  → structured style review as JSON
  POST /ask     { "question": "..." } → Q&A answer as JSON
  GET  /health  → { "status": "ok" }

Run with:
  make server-http
  # or directly:
  .venv/bin/python server_http.py
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

PORT = int(os.getenv("HTTP_SERVER_PORT", "5123"))

# Lazy-import rag so the server starts instantly and initialises on first request
import rag


def _parse_review(raw: str) -> dict:
    """
    Parse the free-text style review into a structured dict the extension can use.
    Returns:
      { verdict, issues: [{severity, rule, found, fix, page}], summary }

    Handles both structured format (X [FAIL] / ! [WARN] lines) and graceful
    fallback when smaller models write violations in prose instead.
    """
    # ── Verdict ───────────────────────────────────────────────────────────────
    verdict = "PASS"
    if "NEEDS REVISION" in raw:
        verdict = "NEEDS REVISION"
    elif "PASS WITH SUGGESTIONS" in raw:
        verdict = "PASS WITH SUGGESTIONS"

    issues = []
    summary = ""

    lines = raw.splitlines()
    i = 0
    in_summary = False
    summary_lines = []

    while i < len(lines):
        line = lines[i].strip()

        # ── Structured issue lines ─────────────────────────────────────────
        # Accept: "X [FAIL]", "! [WARN]", "❌ [FAIL]", "⚠️ [WARN]", "- [FAIL]"
        is_fail = any(line.startswith(p) for p in ("X [FAIL]", "❌ [FAIL]", "- [FAIL]", "[FAIL]"))
        is_warn = any(line.startswith(p) for p in ("! [WARN]", "⚠️ [WARN]", "⚠ [WARN]", "- [WARN]", "[WARN]"))

        if is_fail or is_warn:
            in_summary = False
            severity = "FAIL" if is_fail else "WARN"

            # Extract rule name after the marker
            for marker in ("X [FAIL]", "❌ [FAIL]", "! [WARN]", "⚠️ [WARN]", "⚠ [WARN]", "- [FAIL]", "- [WARN]", "[FAIL]", "[WARN]"):
                if line.startswith(marker):
                    rule_part = line[len(marker):].strip(" -–—:")
                    break
            else:
                rule_part = line

            page = None
            if "(p." in rule_part:
                try:
                    page = int(rule_part.split("(p.")[1].split(")")[0])
                    rule_part = rule_part.split("(p.")[0].strip()
                except ValueError:
                    pass
            rule = rule_part.strip(" -–—:")

            found = ""
            fix = ""
            j = i + 1
            while j < len(lines) and j < i + 8:
                sub = lines[j].strip()
                # Accept "Found:", "**Found**:", "- Found:"
                if any(sub.startswith(p) for p in ("Found:", "**Found**:", "- Found:", "* Found:")):
                    found = sub.split(":", 1)[1].strip().strip('\"\' *')
                elif any(sub.startswith(p) for p in ("Fix:", "**Fix**:", "- Fix:", "* Fix:", "Suggestion:", "Replace with:")):
                    fix = sub.split(":", 1)[1].strip().strip('\"\' *')
                elif sub.startswith("Rule:") or sub.startswith("**Rule**:"):
                    pass  # skip Rule: line, not needed in output
                elif sub and not sub.startswith("-") and found and not fix:
                    # Some models put the fix on the next bare line
                    fix = sub.strip('"\' ')
                j += 1

            issues.append({
                "severity": severity,
                "rule": rule or "Style violation",
                "found": found,
                "fix": fix,
                "page": page,
            })
            i = j
            continue

        # ── Summary section ────────────────────────────────────────────────
        if line in ("SUMMARY", "SUMMARY:", "## Summary") or (line.startswith("SUMMARY") and len(line) < 20):
            in_summary = True
            i += 1
            continue

        if line.startswith("---") or line.startswith("Rules retrieved"):
            in_summary = False
            i += 1
            continue

        if in_summary and line and not line.startswith("="):
            summary_lines.append(line)

        i += 1

    summary = " ".join(summary_lines)

    # ── Fallback: if verdict says issues exist but parser found none ───────
    # Surface the summary as a single synthetic issue so VS Code shows something
    if verdict != "PASS" and not issues and summary:
        issues.append({
            "severity": "WARN",
            "rule": "Style violation (see Output panel for details)",
            "found": "",
            "fix": summary[:200],
            "page": None,
        })

    return {"verdict": verdict, "issues": issues, "summary": summary}


class Handler(BaseHTTPRequestHandler):

    def log_message(self, format: str, *args) -> None:  # type: ignore
        # Suppress default access log noise; keep errors
        if int(args[1]) >= 400 if len(args) > 1 else False:
            super().log_message(format, *args)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def _send_json(self, code: int, payload: dict) -> None:
        try:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # Client disconnected (e.g. extension timed out) — safe to ignore

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "port": PORT})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        try:
            body = self._read_body()
        except Exception as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return

        if self.path == "/check":
            text = body.get("text", "").strip()
            if not text:
                self._send_json(400, {"error": "Missing 'text' field"})
                return
            raw = rag.check_text_style(text)
            self._send_json(200, _parse_review(raw))

        elif self.path == "/ask":
            question = body.get("question", "").strip()
            if not question:
                self._send_json(400, {"error": "Missing 'question' field"})
                return
            answer = rag.query_style_guide(question)
            self._send_json(200, {"answer": answer})

        else:
            self._send_json(404, {"error": "Not found"})


if __name__ == "__main__":
    print(f"Oratio — Style Guide HTTP server starting on http://localhost:{PORT}")
    print("Endpoints: POST /check  POST /ask  GET /health")
    print("Note: first request initialises the RAG pipeline and may take 30-60s.")
    print("      With Ollama, allow 2-3 min for slow models (mistral:7b, llama3.1:8b).")
    print("Press Ctrl+C to stop.\n")
    server = HTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
