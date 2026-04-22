"""Bridge to the user's Chrome for executing merchant checkout playbooks.

Design boundary:
  * We drive the browser up to the point where a HumanGate fires (card entry,
    3DS, magic-link click, MetaMask sign, OTP). At that moment we stop driving
    and let the user finish the step in the tab.
  * We detect completion / decline by polling the DOM for textual or URL-based
    signals declared in the playbook step.
  * No rail-specific logic lives here. `wait_for_metamask_popup` etc. are all
    just convenience wrappers around `wait_for_human:`.

Backends, in order of preference:
  1. Claude-in-Chrome MCP (when the runner has access — translates to semantic
     actions and tolerates UI drift gracefully).
  2. Raw CDP via `pychrome` (what this module actually implements).
  3. Dry-run simulator (`KYA_BROKER_DRY_RUN=1`), used in tests and setup smoke.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import ChromeConfig, MerchantConfig, NotificationConfig
from .human_gate import (
    DEFAULT_COMPLETION_KEYWORDS,
    DEFAULT_DECLINE_KEYWORDS,
    HumanGate,
    HumanGateOutcome,
    HumanGateReason,
    HumanGateRequest,
    build_page_text_predicate,
    build_selector_predicate,
    build_url_predicate,
    default_human_prompt,
)
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


# --------------------------------------------------------------------------
# Template rendering


_TEMPLATE_RE = re.compile(r"\$\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _render_template(value: Any, intent: Intent, merchant: MerchantConfig) -> Any:
    """Substitute ${{ intent.X }} and ${{ merchant.X }} placeholders in strings.

    Nested dicts and lists are walked recursively. Non-string leaves are returned
    unchanged.
    """
    if isinstance(value, str):
        def replace(m: re.Match[str]) -> str:
            path = m.group(1).split(".")
            root = {"intent": intent, "merchant": merchant}.get(path[0])
            if root is None:
                return m.group(0)
            cur: Any = root
            for part in path[1:]:
                cur = getattr(cur, part, None)
                if cur is None:
                    return m.group(0)
            return str(cur)

        return _TEMPLATE_RE.sub(replace, value)
    if isinstance(value, list):
        return [_render_template(v, intent, merchant) for v in value]
    if isinstance(value, dict):
        return {k: _render_template(v, intent, merchant) for k, v in value.items()}
    return value


# --------------------------------------------------------------------------


class ChromeBridge:
    def __init__(
        self,
        cfg: ChromeConfig,
        notifications: NotificationConfig | None = None,
    ):
        self.cfg = cfg
        self.notifications = notifications or NotificationConfig()
        self._backend: str | None = None
        self._human_gate = HumanGate(
            channels=self.notifications.channels,
            poll_interval_s=self.notifications.poll_interval_s,
        )

    # ---- availability -----------------------------------------------

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

    # ---- balance check (crypto only — cards don't expose this) -------

    def query_metamask_balance_usdc(self) -> float | None:
        if _env_flag("KYA_BROKER_DRY_RUN"):
            return float(_env_num("KYA_BROKER_DRY_RUN_BALANCE", 123.45))
        if not self._cdp_reachable():
            raise ChromeUnavailableError("Chrome CDP port unreachable")
        return None  # production impl deferred to M3 hardening

    # ---- playbook execution -----------------------------------------

    async def run_playbook(
        self,
        playbook_name: str,
        intent: Intent,
        merchant: MerchantConfig,
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

        pb = _render_template(pb, intent, merchant)
        return await self._run_cdp(pb, intent, merchant)

    # ---- dry-run simulator -------------------------------------------

    async def _run_dry_run(self, playbook_name: str, intent: Intent) -> PlaybookResult:
        """Deterministic simulator used in tests and the setup smoke test."""
        outcome = _env("KYA_BROKER_DRY_RUN_OUTCOME", "settled")
        delay = _env_num("KYA_BROKER_DRY_RUN_DELAY_S", 0.1)
        await asyncio.sleep(delay)

        if outcome == "user_declined":
            return PlaybookResult(state="user_declined", error="user declined (dry-run)")
        if outcome in {"failed", "timeout"}:
            return PlaybookResult(state="failed", error=f"simulated {outcome}")

        fake_hash = f"0xdr{int(time.time())}{intent.intent_id[:6]}"
        return PlaybookResult(
            state="settled",
            tx_hash=fake_hash,
            merchant_receipt_id=f"dry-{intent.intent_id[:8]}",
            actual_cost_usd=intent.amount_usd,
        )

    # ---- CDP backend -------------------------------------------------

    async def _run_cdp(
        self, pb: dict[str, Any], intent: Intent, merchant: MerchantConfig
    ) -> PlaybookResult:
        try:
            import pychrome  # type: ignore
        except ImportError as e:
            raise ChromeUnavailableError(
                "pychrome not installed. `pip install pychrome` or use KYA_BROKER_DRY_RUN=1"
            ) from e

        browser = pychrome.Browser(url=f"http://127.0.0.1:{self.cfg.cdp_port}")
        tab = browser.new_tab()
        tab.start()

        step: dict[str, Any] = {}
        try:
            tab.Page.enable()
            tab.Runtime.enable()
            tab.DOM.enable()

            for step in pb.get("steps", []):
                result = await self._execute_step(tab, step, intent, merchant)
                if result is not None:
                    return result

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
        self,
        tab: Any,
        step: dict[str, Any],
        intent: Intent,
        merchant: MerchantConfig,
    ) -> PlaybookResult | None:
        """Dispatch a single playbook step. Return PlaybookResult to short-circuit, or None."""
        if "goto" in step:
            tab.Page.navigate(url=step["goto"])
            await asyncio.sleep(1.0)
            return None

        if "click_visual" in step:
            label = step["click_visual"]
            js = (
                'const xp=`//*[contains(normalize-space(text()),"{label}")]`;'
                'const r=document.evaluate(xp,document,null,9,null).singleNodeValue;'
                'if(r){{r.click();return true}}return false'
            ).format(label=label.replace('"', '\\"'))
            tab.Runtime.evaluate(expression=js)
            await asyncio.sleep(0.5)
            return None

        if "fill_amount" in step or "select_amount" in step:
            amount_str = str(step.get("fill_amount") or step.get("select_amount", "")).lstrip("$")
            js = (
                'const i=document.querySelector("input[name*=amount],'
                'input[aria-label*=amount i],input[type=number]");'
                f'if(i){{i.focus();i.value="{amount_str}";'
                'i.dispatchEvent(new Event("input",{bubbles:true}));'
                'i.dispatchEvent(new Event("change",{bubbles:true}));}}'
            )
            tab.Runtime.evaluate(expression=js)
            await asyncio.sleep(0.3)
            return None

        if "select_payment_method" in step:
            # Click an element labeled with the method name (e.g. "Card", "Crypto", "MetaMask")
            label = step["select_payment_method"]
            js = (
                'const xp=`//*[self::button or self::a or self::div or self::label]'
                '[contains(normalize-space(.),"{label}")]`;'
                'const r=document.evaluate(xp,document,null,9,null).singleNodeValue;'
                'if(r){{r.click();return true}}return false'
            ).format(label=label.replace('"', '\\"'))
            tab.Runtime.evaluate(expression=js)
            await asyncio.sleep(0.5)
            return None

        if "wait_for" in step:
            # Basic wait: either a duration or a selector-to-appear.
            what = step["wait_for"]
            if isinstance(what, dict) and "selector" in what:
                timeout = int(what.get("timeout_s", 20))
                deadline = time.time() + timeout
                pred = build_selector_predicate(tab, what["selector"], exists=True)
                while time.time() < deadline:
                    if await pred():
                        return None
                    await asyncio.sleep(0.5)
                return PlaybookResult(
                    state="failed",
                    error=f"wait_for selector {what['selector']!r} never appeared",
                )
            await asyncio.sleep(1.0)
            return None

        if "wait_for_human" in step:
            return await self._handle_human_gate(tab, step["wait_for_human"], intent, merchant)

        # Legacy alias from v0.3.1 — collapse to wait_for_human with metamask reason.
        if "wait_for_metamask_popup" in step:
            legacy = dict(step["wait_for_metamask_popup"] or {})
            legacy.setdefault("reason", "metamask_sign")
            legacy.setdefault("timeout", legacy.get("timeout", "300s"))
            return await self._handle_human_gate(tab, legacy, intent, merchant)

        if "wait_for_merchant_settlement" in step:
            timeout = _parse_seconds(step["wait_for_merchant_settlement"].get("timeout", "300s"))
            pattern = step["wait_for_merchant_settlement"].get("expected_amount", "")
            settled = await self._wait_settlement(tab, pattern, timeout)
            if not settled:
                return PlaybookResult(state="failed", error="merchant settlement timeout")
            return None

        if "record_outcome" in step:
            # No-op — execution records are written by the broker based on the final result.
            return None

        logger.info("chrome: skipping unrecognised step %r", step)
        return None

    async def _handle_human_gate(
        self,
        tab: Any,
        spec: dict[str, Any],
        intent: Intent,
        merchant: MerchantConfig,
    ) -> PlaybookResult | None:
        reason = HumanGateReason(spec.get("reason", "generic"))
        prompt = spec.get("prompt") or default_human_prompt(reason, intent.amount_usd, merchant.name)
        timeout_s = _parse_seconds(spec.get("timeout", self.cfg.human_gate_timeout_s))

        completion_words = list(
            spec.get("detect_completion_keywords")
            or DEFAULT_COMPLETION_KEYWORDS.get(reason, [])
        )
        decline_words = list(
            spec.get("detect_decline_keywords")
            or DEFAULT_DECLINE_KEYWORDS.get(reason, [])
        )

        predicates = []
        if "detect_completion_url" in spec:
            predicates.append(build_url_predicate(tab, spec["detect_completion_url"]))
        if "detect_completion_selector" in spec:
            predicates.append(
                build_selector_predicate(tab, spec["detect_completion_selector"], exists=True)
            )
        if completion_words:
            predicates.append(build_page_text_predicate(tab, completion_words))

        async def on_completion() -> bool:
            for p in predicates:
                if await p():
                    return True
            return False

        decline_predicates = []
        if "detect_decline_selector" in spec:
            decline_predicates.append(
                build_selector_predicate(tab, spec["detect_decline_selector"], exists=True)
            )
        if decline_words:
            decline_predicates.append(build_page_text_predicate(tab, decline_words))

        async def on_decline() -> bool:
            for p in decline_predicates:
                if await p():
                    return True
            return False

        presence_check = None
        optional = bool(spec.get("optional", False))
        if optional and "presence_selector" in spec:
            presence_check = build_selector_predicate(tab, spec["presence_selector"], exists=True)
        elif optional and "presence_keywords" in spec:
            presence_check = build_page_text_predicate(tab, list(spec["presence_keywords"]))

        request = HumanGateRequest(
            reason=reason,
            prompt=prompt,
            timeout_seconds=timeout_s,
            on_completion=on_completion if predicates else None,
            on_decline=on_decline if decline_predicates else None,
            optional=optional,
            presence_check=presence_check,
        )
        result = await self._human_gate.wait_for_human(request)

        if result.outcome == HumanGateOutcome.DECLINED:
            return PlaybookResult(state="user_declined", error=f"user declined at {reason.value}")
        if result.outcome == HumanGateOutcome.TIMEOUT:
            return PlaybookResult(
                state="failed", error=f"human gate {reason.value} timed out after {timeout_s}s"
            )
        # COMPLETED or SKIPPED both continue the playbook.
        return None

    async def _wait_settlement(self, tab: Any, pattern: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        positive = ["credit added", "payment received", "settled", "balance updated"]
        while time.time() < deadline:
            res = tab.Runtime.evaluate(
                expression='document.body?document.body.innerText:""'
            )
            text = (res.get("result", {}).get("value") or "").lower()
            if pattern and pattern.lower() in text:
                return True
            if any(kw in text for kw in positive):
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
            png_b64 = screenshot.get("data", "")
            if png_b64:
                (out_dir / "screenshot.png").write_bytes(base64.b64decode(png_b64))
        except Exception:  # noqa: BLE001
            pass
        (out_dir / "step.txt").write_text(step_context, encoding="utf-8")
        return out_dir


# --------------------------------------------------------------------------


def _parse_seconds(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s.endswith("s"):
        s = s[:-1]
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    return int(float(s))


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_num(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_flag(key: str) -> bool:
    return os.environ.get(key, "").lower() in {"1", "true", "yes", "on"}


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True
