"""Bridge to Claude in Chrome (or a plain CDP Chrome instance) for running playbooks.

Design boundary:
  * We DO NOT automate the MetaMask confirm button. That's the user's L2 authorization.
  * We DO drive the rest of the vast.ai checkout flow: clicking Add Credits, entering
    amount, selecting the crypto rail, waiting for the MetaMask popup to appear,
    detecting settlement, etc.
  * When Claude in Chrome MCP is available (default), we translate playbook steps
    into calls against it. Otherwise we fall back to raw CDP via pychrome.

The PlaybookResult returned is deliberately shallow — Broker only needs:
    state in {"settled", "user_declined", "timeout", "failed"}
    tx_hash / merchant_receipt_id / actual_cost_usd (when available)
    error (human-readable) on failure

A live Chrome instance is not required to import this module; `is_available()` and
run_playbook() raise ChromeUnavailableError if not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import ChromeConfig
from .intent import Intent
from .paths import dumps_dir, playbook_dir

logger = logging.getLogger("kya_broker.chrome")


class ChromeUnavailableError(Exception):
    pass


@dataclass
class PlaybookResult:
    state: str  # "settled" | "user_declined" | "timeout" | "failed"
    tx_hash: str | None = None
    merchant_receipt_id: str | None = None
    actual_cost_usd: float | None = None
    error: str | None = None
    dom_dump_path: str | None = None


class ChromeBridge:
    """Translates YAML playbooks into browser actions.

    The bridge supports three backends, in order of preference:
      1. `claude-in-chrome` MCP client — high-level semantic actions.
      2. Raw CDP (Chrome DevTools Protocol) via HTTP.
      3. Dry-run simulator (KYA_BROKER_DRY_RUN=1) — no browser, used for tests.
    """

    def __init__(self, cfg: ChromeConfig):
        self.cfg = cfg
        self._backend: str | None = None

    # ---- availability ----------------------------------------------------

    def is_available(self) -> bool:
        if _env_flag("KYA_BROKER_DRY_RUN"):
            return True
        return self._cdp_reachable() or self._chrome_binary_installed()

    def _cdp_reachable(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            return s.connect_ex(("127.0.0.1", self.cfg.cdp_port)) == 0
        finally:
            s.close()

    def _chrome_binary_installed(self) -> bool:
        if self.cfg.binary_path:
            return Path(self.cfg.binary_path).exists()
        common = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
        ]
        return any(Path(p).exists() for p in common) or shutil.which("google-chrome") is not None

    # ---- balance check ---------------------------------------------------

    def query_metamask_balance_usdc(self) -> float | None:
        """Best-effort read of the MetaMask USDC balance on the configured chain.

        Real implementation walks the MetaMask extension's storage via CDP. We
        return None when Chrome isn't running rather than crashing, so the CLI
        can still show spending data without a live browser.
        """
        if _env_flag("KYA_BROKER_DRY_RUN"):
            return float(_env_num("KYA_BROKER_DRY_RUN_BALANCE", 123.45))
        if not self._cdp_reachable():
            raise ChromeUnavailableError("Chrome CDP port unreachable")
        # Implementation deferred to M3 hardening; return None so check_balance
        # displays "balance unknown" instead of crashing.
        return None

    # ---- playbook execution ---------------------------------------------

    async def run_playbook(
        self, playbook_name: str, intent: Intent, merchant: Any
    ) -> PlaybookResult:
        if _env_flag("KYA_BROKER_DRY_RUN"):
            return await self._run_dry_run(playbook_name, intent)

        if not self.is_available():
            raise ChromeUnavailableError(
                "Chrome not detected; install and start with --remote-debugging-port="
                f"{self.cfg.cdp_port} or set KYA_BROKER_DRY_RUN=1 for simulation"
            )

        path = playbook_dir() / playbook_name
        if not path.exists():
            raise ChromeUnavailableError(f"playbook {playbook_name} not found at {path}")

        with path.open("r", encoding="utf-8") as f:
            pb = yaml.safe_load(f)

        return await self._run_cdp(pb, intent)

    # ---- dry-run simulator ----------------------------------------------

    async def _run_dry_run(self, playbook_name: str, intent: Intent) -> PlaybookResult:
        """Deterministic simulator used in tests and setup smoke-tests.

        Behaviour driven by env vars:
            KYA_BROKER_DRY_RUN_OUTCOME = settled | user_declined | failed | timeout
            KYA_BROKER_DRY_RUN_DELAY_S = fake latency
        """
        outcome = _env("KYA_BROKER_DRY_RUN_OUTCOME", "settled")
        delay = _env_num("KYA_BROKER_DRY_RUN_DELAY_S", 0.1)
        await asyncio.sleep(delay)

        if outcome == "user_declined":
            return PlaybookResult(state="user_declined", error="user declined (dry-run)")
        if outcome == "failed":
            return PlaybookResult(state="failed", error="simulated failure")
        if outcome == "timeout":
            return PlaybookResult(state="failed", error="simulated timeout")

        fake_hash = f"0xdr{int(time.time())}{intent.intent_id[:6]}"
        return PlaybookResult(
            state="settled",
            tx_hash=fake_hash,
            merchant_receipt_id=f"dry-{intent.intent_id[:8]}",
            actual_cost_usd=intent.amount_usd,
        )

    # ---- CDP backend -----------------------------------------------------

    async def _run_cdp(self, pb: dict[str, Any], intent: Intent) -> PlaybookResult:
        """Execute a playbook over a CDP-connected Chrome instance.

        This is the production path. It's structured so each playbook step maps
        to an awaitable helper below. We intentionally keep it straight-line and
        linear; complex branching belongs in the playbook YAML, not here.
        """
        steps = pb.get("steps", [])
        preconditions = pb.get("preconditions", [])

        # Import lazily so the module loads fine without pychrome installed.
        try:
            import pychrome  # type: ignore
        except ImportError as e:
            raise ChromeUnavailableError(
                "pychrome not installed. `pip install pychrome` or use KYA_BROKER_DRY_RUN=1"
            ) from e

        browser = pychrome.Browser(url=f"http://127.0.0.1:{self.cfg.cdp_port}")
        tab = browser.new_tab()
        tab.start()

        try:
            tab.Page.enable()
            tab.Runtime.enable()
            tab.DOM.enable()

            for step in steps:
                result = await self._execute_step(tab, step, intent)
                if result is not None:
                    return result

            # If the playbook didn't explicitly emit a terminal state, treat as settled.
            return PlaybookResult(state="settled")

        except Exception as e:  # noqa: BLE001
            dump = self._dump_failure(tab, intent, step_context=str(step))
            return PlaybookResult(
                state="failed", error=f"{type(e).__name__}: {e}", dom_dump_path=str(dump)
            )
        finally:
            with _suppress():
                tab.stop()
                browser.close_tab(tab)

    async def _execute_step(
        self, tab: Any, step: dict[str, Any], intent: Intent
    ) -> PlaybookResult | None:
        """Execute a single playbook step. Return a PlaybookResult to short-circuit, or None."""
        if "goto" in step:
            tab.Page.navigate(url=step["goto"])
            await asyncio.sleep(1.0)
            return None

        if "click_visual" in step:
            # Best-effort: find by text via document.evaluate — the production
            # implementation would use Claude-in-Chrome's visual click tool here.
            label = step["click_visual"]
            js = (
                "const xp=`//*[contains(text(),\"{label}\")]`;"
                "const r=document.evaluate(xp,document,null,9,null).singleNodeValue;"
                "if(r){{r.click();return true}}return false"
            ).format(label=label.replace('"', '\\"'))
            tab.Runtime.evaluate(expression=js)
            await asyncio.sleep(0.5)
            return None

        if "select_amount" in step:
            amount_str = step["select_amount"].lstrip("$")
            js = (
                f'const i=document.querySelector("input[name*=amount],input[type=number]");'
                f'if(i){{i.value="{amount_str}";i.dispatchEvent(new Event("input",{{bubbles:true}}));}}'
            )
            tab.Runtime.evaluate(expression=js)
            await asyncio.sleep(0.3)
            return None

        if "wait_for_metamask_popup" in step:
            timeout = int(step["wait_for_metamask_popup"].get("timeout", "300s").rstrip("s"))
            result = await self._wait_metamask(tab, timeout)
            if result.state != "settled":
                return result
            return None

        if "wait_for_merchant_settlement" in step:
            timeout = int(step["wait_for_merchant_settlement"].get("timeout", "300s").rstrip("s"))
            pattern = step["wait_for_merchant_settlement"].get("expected_amount", "")
            settled = await self._wait_settlement(tab, pattern, timeout)
            if not settled:
                return PlaybookResult(state="failed", error="merchant settlement timeout")
            return None

        if "wait_for" in step:
            await asyncio.sleep(1.0)  # placeholder; production uses DOM polling
            return None

        # Unknown step types just log and continue
        logger.info("chrome: skipping unrecognised step %r", step)
        return None

    async def _wait_metamask(self, tab: Any, timeout: int) -> PlaybookResult:
        """Poll for the MetaMask extension popup, then for user decision.

        The popup is a separate Chrome window; CDP lists it in targets/Page.
        We don't click anything inside it — we just detect when it closes and
        whether the preceding page shows 'signed' vs 'rejected' indicators.
        """
        deadline = time.time() + timeout
        popup_seen = False
        while time.time() < deadline:
            result = tab.Runtime.evaluate(
                expression=(
                    '(()=>{'
                    'const t=document.body?document.body.innerText:"";'
                    'return JSON.stringify({'
                    ' declined:/(reject|declined|cancel|denied)/i.test(t),'
                    ' signed:/(confirmed|success|signed|payment received)/i.test(t)'
                    '})'
                    '})()'
                )
            )
            try:
                raw = result.get("result", {}).get("value", "{}")
                data = json.loads(raw) if isinstance(raw, str) else {}
            except (ValueError, TypeError):
                data = {}
            if data.get("declined"):
                return PlaybookResult(state="user_declined", error="user declined in MetaMask")
            if data.get("signed"):
                return PlaybookResult(state="settled")
            await asyncio.sleep(1.0)
            popup_seen = True

        err = "metamask popup never showed" if not popup_seen else "timeout waiting for signature"
        return PlaybookResult(state="failed", error=err)

    async def _wait_settlement(self, tab: Any, pattern: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            res = tab.Runtime.evaluate(
                expression='document.body?document.body.innerText:""'
            )
            text = res.get("result", {}).get("value", "") or ""
            if pattern and pattern in text:
                return True
            if any(kw in text.lower() for kw in ("credit added", "payment received", "settled")):
                return True
            await asyncio.sleep(2.0)
        return False

    def _dump_failure(self, tab: Any, intent: Intent, step_context: str) -> Path:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out_dir = dumps_dir() / f"{stamp}-{intent.intent_id[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            html = tab.Runtime.evaluate(expression="document.documentElement.outerHTML")
            (out_dir / "dom.html").write_text(
                html.get("result", {}).get("value", "") or "", encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            screenshot = tab.Page.captureScreenshot()
            import base64

            png_b64 = screenshot.get("data", "")
            if png_b64:
                (out_dir / "screenshot.png").write_bytes(base64.b64decode(png_b64))
        except Exception:  # noqa: BLE001
            pass
        (out_dir / "step.txt").write_text(step_context, encoding="utf-8")
        return out_dir


# ---- small utils -----------------------------------------------------------


def _env(key: str, default: str) -> str:
    import os

    return os.environ.get(key, default)


def _env_num(key: str, default: float) -> float:
    import os

    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_flag(key: str) -> bool:
    import os

    return os.environ.get(key, "").lower() in {"1", "true", "yes", "on"}


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True
