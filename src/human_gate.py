"""HumanGate — the generic L2-authorization primitive.

Every payment rail ends in a moment where the human has to do something the
agent physically cannot do:

  * Sign a transaction in MetaMask (password + confirm)
  * Type / autofill a credit card and click Pay
  * Complete a 3D-Secure challenge from the issuing bank
  * Click a magic link in their email
  * Enter an OTP from SMS / authenticator

All of these are the same shape: "surface the moment clearly, wait for done or
declined, return the outcome." This module centralises that shape so the
broker doesn't have to special-case per rail.

The HumanGate does NOT automate any of these actions. It surfaces them and
polls for completion. The actual doing is the user's.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from rich.console import Console
from rich.panel import Panel


class HumanGateReason(str, Enum):
    METAMASK_SIGN = "metamask_sign"       # sign a transaction in MetaMask extension
    CARD_DETAILS = "card_details"         # enter credit card in merchant / Stripe / etc.
    CARD_3DS = "card_3ds"                 # complete 3D-Secure challenge from bank
    EMAIL_MAGIC_LINK = "email_magic_link" # click link in an email to continue
    EMAIL_OTP = "email_otp"               # copy OTP from email into a form
    SMS_OTP = "sms_otp"                   # copy OTP from SMS
    LOGIN = "login"                       # log into the merchant account
    SAVED_CARD_CONFIRM = "saved_card_confirm"  # confirm charge on already-saved card
    PASSKEY = "passkey"                   # passkey / biometric confirmation
    GENERIC = "generic"


class HumanGateOutcome(str, Enum):
    COMPLETED = "completed"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


# Predicate returning True when the condition is observed on the page.
# Called repeatedly; should be cheap and idempotent.
Predicate = Callable[[], Awaitable[bool]]


@dataclass
class HumanGateRequest:
    reason: HumanGateReason
    prompt: str
    timeout_seconds: int
    on_completion: Predicate | None = None
    on_decline: Predicate | None = None
    # If True, a short "not-present" check at the start skips the gate entirely
    # (e.g. 3DS only appears for some cards — skip if no challenge element exists).
    optional: bool = False
    presence_check: Predicate | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HumanGateResult:
    outcome: HumanGateOutcome
    duration_ms: int
    reason: HumanGateReason
    metadata: dict[str, Any] = field(default_factory=dict)


class HumanGate:
    """Surfaces human-action requests and waits for the user to act.

    Notification channels:
      - "terminal": rich console panel (always on)
      - "osascript_notify": macOS banner notification via `osascript`
      - "osascript_say": speaks the reason aloud on macOS
      - custom callable: add your own (Slack webhook, push notification, etc.)

    The `poll_interval_s` trades responsiveness vs Chrome CDP chattiness.
    Default 1.0s feels instant to a human and keeps CDP load negligible.
    """

    def __init__(
        self,
        channels: list[str] | None = None,
        poll_interval_s: float = 1.0,
        custom_notifier: Callable[[HumanGateRequest], None] | None = None,
    ) -> None:
        self.channels = channels or ["terminal"]
        if os.environ.get("KYA_BROKER_HUMAN_GATE_NOTIFY_OS") == "1":
            self.channels = list({*self.channels, "osascript_notify"})
        self.poll_interval_s = poll_interval_s
        self.custom_notifier = custom_notifier
        self._console = Console()

    # ---- dry-run override --------------------------------------------

    @staticmethod
    def _dry_run_outcome() -> HumanGateOutcome | None:
        """When KYA_BROKER_DRY_RUN=1, skip actual waiting and return the pre-set outcome."""
        if os.environ.get("KYA_BROKER_DRY_RUN") not in {"1", "true", "yes"}:
            return None
        raw = os.environ.get("KYA_BROKER_DRY_RUN_HUMAN_GATE", "completed").lower()
        try:
            return HumanGateOutcome(raw)
        except ValueError:
            return HumanGateOutcome.COMPLETED

    # ---- main entry point --------------------------------------------

    async def wait_for_human(self, request: HumanGateRequest) -> HumanGateResult:
        start = time.perf_counter()

        # Dry-run short-circuit, used by tests and the setup-wizard smoke test.
        forced = self._dry_run_outcome()
        if forced is not None:
            await asyncio.sleep(0.01)
            return HumanGateResult(
                outcome=forced,
                duration_ms=int((time.perf_counter() - start) * 1000),
                reason=request.reason,
                metadata={"dry_run": True},
            )

        # Optional gate: skip if the page doesn't show a challenge.
        if request.optional and request.presence_check is not None:
            try:
                present = await request.presence_check()
            except Exception:  # noqa: BLE001
                present = True  # err on the side of waiting for human
            if not present:
                return HumanGateResult(
                    outcome=HumanGateOutcome.SKIPPED,
                    duration_ms=int((time.perf_counter() - start) * 1000),
                    reason=request.reason,
                )

        self._notify(request)

        deadline = start + request.timeout_seconds
        while time.perf_counter() < deadline:
            # decline check first — if the user dismissed a modal we want to
            # exit fast rather than wait the full timeout.
            if request.on_decline is not None:
                try:
                    if await request.on_decline():
                        return HumanGateResult(
                            outcome=HumanGateOutcome.DECLINED,
                            duration_ms=int((time.perf_counter() - start) * 1000),
                            reason=request.reason,
                        )
                except Exception:  # noqa: BLE001
                    pass
            if request.on_completion is not None:
                try:
                    if await request.on_completion():
                        return HumanGateResult(
                            outcome=HumanGateOutcome.COMPLETED,
                            duration_ms=int((time.perf_counter() - start) * 1000),
                            reason=request.reason,
                        )
                except Exception:  # noqa: BLE001
                    pass

            await asyncio.sleep(self.poll_interval_s)

        return HumanGateResult(
            outcome=HumanGateOutcome.TIMEOUT,
            duration_ms=int((time.perf_counter() - start) * 1000),
            reason=request.reason,
        )

    # ---- notifications ----------------------------------------------

    def _notify(self, request: HumanGateRequest) -> None:
        for ch in self.channels:
            try:
                if ch == "terminal":
                    self._notify_terminal(request)
                elif ch == "osascript_notify":
                    self._notify_macos(request)
                elif ch == "osascript_say":
                    self._notify_macos_say(request)
            except Exception:  # noqa: BLE001
                # Never let a notifier failure block the actual gate.
                pass
        if self.custom_notifier is not None:
            try:
                self.custom_notifier(request)
            except Exception:  # noqa: BLE001
                pass

    def _notify_terminal(self, request: HumanGateRequest) -> None:
        self._console.print(
            Panel.fit(
                f"[bold]{request.prompt}[/]\n\n"
                f"[dim]reason: {request.reason.value} · "
                f"waiting up to {request.timeout_seconds}s[/]",
                title="[bold yellow]🔔 ACTION NEEDED IN BROWSER[/]",
                border_style="yellow",
            )
        )

    def _notify_macos(self, request: HumanGateRequest) -> None:
        if not shutil.which("osascript"):
            return
        title = "KYA-Broker needs you"
        body = request.prompt.replace('"', "'")[:200]
        script = f'display notification "{body}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)

    def _notify_macos_say(self, request: HumanGateRequest) -> None:
        if not shutil.which("say"):
            return
        text = f"Action needed: {request.reason.value}"
        subprocess.run(["say", text], check=False, capture_output=True)


# ---- declarative playbook step helpers ------------------------------

def build_page_text_predicate(
    tab: Any, keywords: list[str]
) -> Predicate:
    """Return a predicate that returns True when any keyword is in the visible page text."""

    async def _check() -> bool:
        if tab is None:
            return False
        try:
            res = tab.Runtime.evaluate(
                expression="document.body ? document.body.innerText : ''"
            )
            text = (res.get("result", {}).get("value") or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return any(k.lower() in text for k in keywords)

    return _check


def build_url_predicate(tab: Any, url_contains: str) -> Predicate:
    """Return a predicate that returns True when the page URL contains the substring."""

    async def _check() -> bool:
        if tab is None:
            return False
        try:
            res = tab.Runtime.evaluate(expression="window.location.href")
            url = res.get("result", {}).get("value") or ""
        except Exception:  # noqa: BLE001
            return False
        return url_contains in url

    return _check


def build_selector_predicate(tab: Any, selector: str, exists: bool = True) -> Predicate:
    """Return a predicate checking whether a CSS selector matches any element."""

    async def _check() -> bool:
        if tab is None:
            return False
        try:
            expr = f"!!document.querySelector({selector!r})"
            res = tab.Runtime.evaluate(expression=expr)
            hit = bool(res.get("result", {}).get("value"))
        except Exception:  # noqa: BLE001
            hit = False
        return hit if exists else not hit

    return _check


DEFAULT_COMPLETION_KEYWORDS: dict[HumanGateReason, list[str]] = {
    HumanGateReason.METAMASK_SIGN: [
        "confirmed", "transaction sent", "transaction confirmed", "payment received",
        "signature verified", "signed",
    ],
    HumanGateReason.CARD_DETAILS: [
        "payment successful", "payment succeeded", "thank you", "receipt",
        "credit added", "balance updated",
    ],
    HumanGateReason.CARD_3DS: [
        "verified", "3ds complete", "authentication successful", "payment successful",
    ],
    HumanGateReason.EMAIL_MAGIC_LINK: [
        "signed in", "logged in", "welcome back", "dashboard",
    ],
    HumanGateReason.EMAIL_OTP: [
        "verified", "verification successful", "code accepted",
    ],
    HumanGateReason.SMS_OTP: [
        "verified", "verification successful", "code accepted",
    ],
    HumanGateReason.LOGIN: ["dashboard", "welcome", "signed in", "sign out"],
    HumanGateReason.SAVED_CARD_CONFIRM: [
        "payment successful", "thank you", "confirmed", "credit added",
    ],
    HumanGateReason.PASSKEY: ["authenticated", "verified", "signed in"],
    HumanGateReason.GENERIC: ["success", "confirmed", "complete"],
}


DEFAULT_DECLINE_KEYWORDS: dict[HumanGateReason, list[str]] = {
    HumanGateReason.METAMASK_SIGN: ["rejected", "declined", "cancel", "denied by user"],
    HumanGateReason.CARD_DETAILS: ["declined", "card declined", "insufficient funds"],
    HumanGateReason.CARD_3DS: ["authentication failed", "3ds failed", "declined"],
    HumanGateReason.EMAIL_MAGIC_LINK: ["expired", "invalid link"],
    HumanGateReason.EMAIL_OTP: ["invalid code", "code expired", "too many attempts"],
    HumanGateReason.SMS_OTP: ["invalid code", "code expired", "too many attempts"],
    HumanGateReason.LOGIN: ["invalid credentials", "incorrect password"],
    HumanGateReason.SAVED_CARD_CONFIRM: ["declined", "cancel"],
    HumanGateReason.PASSKEY: ["authentication failed", "cancelled"],
    HumanGateReason.GENERIC: ["declined", "cancelled", "rejected", "failed"],
}


def default_human_prompt(reason: HumanGateReason, amount_usd: float, merchant: str) -> str:
    """Canonical user-facing instructions for each gate type."""
    return {
        HumanGateReason.METAMASK_SIGN: (
            f"Open your Chrome tab for {merchant}. The MetaMask extension popup should be "
            f"asking you to sign a payment of ${amount_usd:.2f}. Review the amount and "
            f"recipient, then click Confirm."
        ),
        HumanGateReason.CARD_DETAILS: (
            f"Open your Chrome tab for {merchant}. A credit-card checkout form is visible. "
            f"Either autofill with your saved card (1Password / Chrome / Apple Pay) or enter "
            f"the card manually, then click Pay to charge ${amount_usd:.2f}."
        ),
        HumanGateReason.CARD_3DS: (
            "Your bank is presenting a 3D-Secure challenge (one-time code, push, or biometric). "
            "Complete it in the Chrome tab. This is normal for card payments."
        ),
        HumanGateReason.EMAIL_MAGIC_LINK: (
            f"{merchant} has sent you an email with a login or confirmation link. Open your "
            f"inbox in another tab, click the link, then return to the Chrome tab to continue."
        ),
        HumanGateReason.EMAIL_OTP: (
            f"{merchant} has sent a one-time code to your email. Copy it into the form in "
            f"the Chrome tab."
        ),
        HumanGateReason.SMS_OTP: (
            f"{merchant} has sent a one-time code to your phone. Copy it into the form in "
            f"the Chrome tab."
        ),
        HumanGateReason.LOGIN: (
            f"Please sign in to {merchant} in the Chrome tab. Google OAuth, email + password, "
            f"whatever the merchant offers. The skill will continue once you're logged in."
        ),
        HumanGateReason.SAVED_CARD_CONFIRM: (
            f"{merchant} is asking you to confirm a ${amount_usd:.2f} charge on your saved "
            f"payment method. Click Confirm in the Chrome tab."
        ),
        HumanGateReason.PASSKEY: (
            "Complete the passkey / biometric prompt in the Chrome tab (Touch ID, Face ID, "
            "security key)."
        ),
        HumanGateReason.GENERIC: (
            f"An action is needed in the Chrome tab for {merchant}. Follow the on-screen "
            f"instructions and return here when done."
        ),
    }[reason]
