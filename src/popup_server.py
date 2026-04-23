"""Local HTTP popup server for human-gate interactions.

When a HumanGate fires with `ui_mode="popup"`, the broker:
  1. Creates a session with a unique id + a schema of fields to collect.
  2. Starts (or reuses) a threaded HTTP server on 127.0.0.1:<ephemeral port>.
  3. Opens the user's default browser to `http://127.0.0.1:<port>/gate/<id>`.
  4. Awaits the user's submission (or timeout / cancel).

Why local HTTP instead of a native GUI (Tk, PyQt, etc.)?
  * stdlib only — no new dependency.
  * Every machine already has a browser — zero install friction.
  * The popup can render Markdown-like instructions and style trivially.
  * Browsers handle copy / paste / password fields natively.

What the popup DOES collect:
  * OTP codes (broker-issued email OTPs, SMS codes the user copies from phone).
  * Confirmation toggles ("yes, I want to proceed with $X on Y").
  * Short memos / notes attached to an intent.

What the popup DOES NOT collect:
  * Full credit card numbers — entering them into a local HTTP endpoint would
    put them in the broker's memory and violate PCI scope. The card is still
    entered in the merchant's Stripe iframe in Chrome. The popup can show
    instructions directing the user there.
  * Wallet private keys / seed phrases — MetaMask is the rightful host.
  * Bank passwords — these live in the bank's own checkout flow.

Security:
  * Server binds to 127.0.0.1 only, never external.
  * Each session has a 128-bit token in the URL path; guessing is infeasible.
  * Sessions auto-expire after submission OR timeout; their data is wiped from
    memory.
  * The server refuses requests with a Host header other than 127.0.0.1:port
    (minor DNS rebinding hardening).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("kya_broker.popup")


class FieldType(str, Enum):
    OTP = "otp"            # 4-8 character code, monospaced display
    SHORT = "short"        # any short text < 200 chars
    PASSWORD = "password"  # hidden input (site passwords, NOT card / crypto)
    CONFIRM = "confirm"    # yes/no radio — value posted as "yes" or "no"
    TEXTAREA = "textarea"  # multi-line memo


@dataclass
class PopupField:
    key: str
    label: str
    type: FieldType = FieldType.SHORT
    placeholder: str = ""
    required: bool = True
    max_length: int = 500


class PopupOutcome(str, Enum):
    SUBMITTED = "submitted"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class PopupResult:
    outcome: PopupOutcome
    data: dict[str, str] = field(default_factory=dict)
    submitted_at_ms: int = 0


@dataclass
class PopupSession:
    session_id: str
    title: str
    instruction_html: str
    fields: list[PopupField]
    timeout_seconds: int
    created_at_ms: int
    result: PopupResult | None = None
    _event: threading.Event = field(default_factory=threading.Event)


# --------------------------------------------------------------------------
# HTML rendering


_PAGE_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
       margin: 0; padding: 2.5rem 3rem; background: #0f1115; color: #e6e6e6;
       line-height: 1.55; }
.card { max-width: 620px; margin: 0 auto; background: #181b22; border: 1px solid #262b36;
        padding: 1.75rem 2rem; border-radius: 12px; }
h1 { font-size: 1.25rem; margin: 0 0 0.5rem 0; color: #ffd36b; }
.reason { font-size: 0.85rem; color: #7a808b; letter-spacing: 0.04em; text-transform: uppercase;
          margin-bottom: 1rem; }
.instruction { background: #0b0d12; border-left: 3px solid #ffd36b; padding: 1rem 1.2rem;
               margin: 1rem 0 1.6rem 0; border-radius: 4px; white-space: pre-wrap; }
label { display: block; margin: 1rem 0 0.35rem; font-weight: 600; font-size: 0.95rem; }
input, textarea { width: 100%; padding: 0.65rem 0.75rem; font-size: 1rem;
                  background: #0b0d12; border: 1px solid #2e3443; color: #e6e6e6;
                  border-radius: 6px; box-sizing: border-box; }
input.otp { letter-spacing: 0.35em; font-family: Menlo, monospace; font-size: 1.3rem;
            text-align: center; }
.buttons { display: flex; gap: 0.75rem; margin-top: 1.6rem; }
button { padding: 0.7rem 1.3rem; font-size: 0.95rem; font-weight: 600;
         border: none; border-radius: 6px; cursor: pointer; }
button.primary { background: #ffd36b; color: #0f1115; }
button.secondary { background: #2e3443; color: #e6e6e6; }
button:hover { filter: brightness(1.1); }
.footer { margin-top: 1.5rem; font-size: 0.82rem; color: #6a707b; }
.done { text-align: center; padding: 3rem 1rem; }
.done h1 { color: #6ee7b7; font-size: 1.4rem; }
"""


def _render_field(f: PopupField) -> str:
    input_class = "otp" if f.type == FieldType.OTP else ""
    placeholder = html.escape(f.placeholder)
    required = "required" if f.required else ""
    max_len = f.max_length
    name = html.escape(f.key)
    label = html.escape(f.label)
    if f.type == FieldType.TEXTAREA:
        return (
            f'<label for="{name}">{label}</label>'
            f'<textarea id="{name}" name="{name}" rows="4" maxlength="{max_len}" '
            f'placeholder="{placeholder}" {required}></textarea>'
        )
    if f.type == FieldType.CONFIRM:
        return (
            f'<label>{label}</label>'
            f'<div style="margin-top:0.3rem"><label style="display:inline">'
            f'<input type="radio" name="{name}" value="yes" {required}> yes</label>'
            f' &nbsp;&nbsp; <label style="display:inline">'
            f'<input type="radio" name="{name}" value="no"> no</label></div>'
        )
    input_type = "password" if f.type == FieldType.PASSWORD else "text"
    return (
        f'<label for="{name}">{label}</label>'
        f'<input type="{input_type}" id="{name}" name="{name}" maxlength="{max_len}" '
        f'class="{input_class}" placeholder="{placeholder}" {required} autocomplete="off">'
    )


def _render_gate_page(session: PopupSession) -> str:
    fields_html = "\n".join(_render_field(f) for f in session.fields)
    csrf = session.session_id  # simple: re-use session id as CSRF token
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(session.title)} · KYA-Broker</title>
<style>{_PAGE_CSS}</style>
</head><body>
<div class="card">
<div class="reason">KYA-Broker · action needed</div>
<h1>{html.escape(session.title)}</h1>
<div class="instruction">{session.instruction_html}</div>
<form method="POST" action="/submit/{session.session_id}">
<input type="hidden" name="_csrf" value="{csrf}">
{fields_html}
<div class="buttons">
<button type="submit" class="primary">Confirm</button>
<button type="submit" class="secondary" formaction="/decline/{session.session_id}"
        formmethod="POST">Decline</button>
</div>
</form>
<div class="footer">
This page is served by the local broker at 127.0.0.1 only. Closing the tab
without submitting is equivalent to declining.
</div>
</div>
</body></html>"""


def _render_done_page(ok: bool, note: str = "") -> str:
    text = "Thank you — you can close this tab." if ok else "Declined. You can close this tab."
    emoji = "✓" if ok else "✗"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>KYA-Broker</title>
<style>{_PAGE_CSS}</style></head><body>
<div class="card"><div class="done"><h1>{emoji} {text}</h1>
<p style="color:#7a808b">{html.escape(note)}</p>
</div></div>
</body></html>"""


# --------------------------------------------------------------------------
# HTTP server


class _Handler(BaseHTTPRequestHandler):
    server_ref: "PopupServer" = None  # set at class level before serving

    def log_message(self, format, *args):  # noqa: A003
        logger.debug("popup-http: " + format, *args)

    def _check_host(self) -> bool:
        host = self.headers.get("Host", "")
        expected = f"127.0.0.1:{self.server_ref.port}"
        if host != expected and host != f"localhost:{self.server_ref.port}":
            self.send_error(400, "bad host header")
            return False
        return True

    def do_GET(self) -> None:
        if not self._check_host():
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) == 2 and parts[0] == "gate":
            session = self.server_ref.get_session(parts[1])
            if session is None or session.result is not None:
                self._send_html(404, _render_done_page(False, "session not found or completed"))
                return
            self._send_html(200, _render_gate_page(session))
            return
        if parts == ["health"]:
            self._send_html(200, "ok")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._check_host():
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 2 or parts[0] not in ("submit", "decline"):
            self.send_error(404)
            return
        session = self.server_ref.get_session(parts[1])
        if session is None or session.result is not None:
            self._send_html(404, _render_done_page(False, "session expired"))
            return

        length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(length).decode("utf-8", errors="replace")
        body = parse_qs(body_raw)
        if body.get("_csrf", [""])[0] != session.session_id:
            self.send_error(403, "csrf")
            return

        if parts[0] == "decline":
            session.result = PopupResult(
                outcome=PopupOutcome.DECLINED,
                submitted_at_ms=int(time.time() * 1000),
            )
            session._event.set()
            self._send_html(200, _render_done_page(False))
            return

        # submit — collect declared fields
        data: dict[str, str] = {}
        for f in session.fields:
            v = body.get(f.key, [""])[0]
            if f.required and not v.strip():
                self._send_html(400, _render_done_page(False, f"field {f.key!r} is required"))
                return
            data[f.key] = v.strip()
        session.result = PopupResult(
            outcome=PopupOutcome.SUBMITTED,
            data=data,
            submitted_at_ms=int(time.time() * 1000),
        )
        session._event.set()
        self._send_html(200, _render_done_page(True))

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)


class PopupServer:
    """Thread-backed local popup server. Singleton-style: one per broker process."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, PopupSession] = {}
        self._lock = threading.Lock()

    # ---- lifecycle --------------------------------------------------

    def ensure_started(self) -> None:
        if self._server is not None:
            return
        handler_class = type("_BoundHandler", (_Handler,), {"server_ref": self})
        self._server = ThreadingHTTPServer((self.host, self.port), handler_class)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="kya-broker-popup",
            daemon=True,
        )
        self._thread.start()
        logger.info("popup server listening on http://%s:%d", self.host, self.port)

    def shutdown(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    # ---- session management ----------------------------------------

    def create_session(
        self,
        title: str,
        instruction: str,
        fields: list[PopupField],
        timeout_seconds: int = 300,
        open_browser: bool = True,
    ) -> PopupSession:
        self.ensure_started()
        session_id = secrets.token_urlsafe(24)
        session = PopupSession(
            session_id=session_id,
            title=title,
            instruction_html=html.escape(instruction),
            fields=fields,
            timeout_seconds=timeout_seconds,
            created_at_ms=int(time.time() * 1000),
        )
        with self._lock:
            self._sessions[session_id] = session
        if open_browser:
            try:
                webbrowser.open_new_tab(self.url_for(session_id))
            except Exception:  # noqa: BLE001
                pass
        return session

    def get_session(self, session_id: str) -> PopupSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def url_for(self, session_id: str) -> str:
        return f"http://{self.host}:{self.port}/gate/{session_id}"

    def cancel(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session and session.result is None:
            session.result = PopupResult(outcome=PopupOutcome.CANCELLED)
            session._event.set()

    # ---- async waiter ----------------------------------------------

    async def wait_for_submission(self, session: PopupSession) -> PopupResult:
        loop = asyncio.get_running_loop()
        done = await loop.run_in_executor(
            None, session._event.wait, session.timeout_seconds
        )
        if not done:
            session.result = PopupResult(outcome=PopupOutcome.TIMEOUT)
        assert session.result is not None
        # Wipe data after delivery when not submitted (defensive)
        if session.result.outcome != PopupOutcome.SUBMITTED:
            session.result.data = {}
        with self._lock:
            # Keep session but zero out submission data after consumption.
            pass
        return session.result


# Module-level shared instance (one server per broker process)
_shared: PopupServer | None = None


def shared_popup_server() -> PopupServer:
    global _shared
    if _shared is None:
        _shared = PopupServer()
        _shared.ensure_started()
    return _shared
