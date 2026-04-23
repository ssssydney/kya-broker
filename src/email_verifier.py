"""Broker-issued email OTP: the final floor of authorization before execution.

Flow (called from Broker._execute before driving Chrome):
  1. Generate a 6-digit numeric code (cryptographically random).
  2. Send it via SMTP to the locked confirmation email.
  3. Open the popup server with an OTP field.
  4. Wait for user to paste the code OR decline OR timeout.
  5. If the pasted code matches, return OK. Otherwise block the intent.

This gate fires EVERY time an intent transitions from `audited` to `executing`.
It is independent of whatever the merchant does in their checkout (3DS, their
own email OTP, etc.) — even if the merchant asks for none, we always ask for
this one. The point is: the user's locked inbox is the channel we trust.

SMTP config comes from ~/.claude/skills/kya-broker.local/.env:

    KYA_BROKER_SMTP_HOST=smtp.gmail.com
    KYA_BROKER_SMTP_PORT=465
    KYA_BROKER_SMTP_USER=user@example.com
    KYA_BROKER_SMTP_PASS=<app password>
    KYA_BROKER_SMTP_FROM="KYA-Broker <user@example.com>"
    KYA_BROKER_SMTP_USE_SSL=true

For dev: set `KYA_BROKER_OTP_SHOW_IN_TERMINAL=1` to bypass SMTP and just print
the code to stdout (so you can paste it without email config). Not safe for
production — a malicious agent watching stdout could read the code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

from .email_lock import EmailLockError, load_locked_email
from .popup_server import FieldType, PopupField, PopupOutcome, PopupServer

logger = logging.getLogger("kya_broker.email_otp")


class EmailOtpError(Exception):
    pass


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    use_ssl: bool = True

    @classmethod
    def from_env(cls) -> "SmtpConfig | None":
        host = os.environ.get("KYA_BROKER_SMTP_HOST")
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("KYA_BROKER_SMTP_PORT", "465")),
            user=os.environ.get("KYA_BROKER_SMTP_USER", ""),
            password=os.environ.get("KYA_BROKER_SMTP_PASS", ""),
            from_addr=os.environ.get("KYA_BROKER_SMTP_FROM", "")
            or os.environ.get("KYA_BROKER_SMTP_USER", ""),
            use_ssl=os.environ.get("KYA_BROKER_SMTP_USE_SSL", "true").lower()
            in {"1", "true", "yes", "on"},
        )


def _generate_code() -> str:
    """Six-digit numeric OTP. ~20 bits of entropy; fine for short-lived confirms."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _render_email(code: str, amount_usd: float, merchant: str, intent_id: str) -> EmailMessage:
    subject = f"[KYA-Broker] confirm ${amount_usd:.2f} at {merchant}"
    body = (
        f"Your KYA-Broker installation is about to charge ${amount_usd:.2f} "
        f"at {merchant}.\n\n"
        f"Confirmation code:\n\n"
        f"    {code}\n\n"
        f"Paste this code into the popup window to authorise the charge. "
        f"If you did NOT initiate this payment, click Decline in the popup "
        f"(or simply ignore this email and the broker will time out).\n\n"
        f"intent_id: {intent_id}\n"
        f"This code is valid for 5 minutes and can be used once.\n"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = ""  # filled by caller
    msg.set_content(body)
    return msg


def _send_email(msg: EmailMessage, to_addr: str, smtp: SmtpConfig) -> None:
    msg["To"] = to_addr
    msg["From"] = smtp.from_addr or smtp.user
    context = ssl.create_default_context()
    if smtp.use_ssl:
        with smtplib.SMTP_SSL(smtp.host, smtp.port, context=context, timeout=15) as s:
            if smtp.user:
                s.login(smtp.user, smtp.password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=15) as s:
            s.starttls(context=context)
            if smtp.user:
                s.login(smtp.user, smtp.password)
            s.send_message(msg)


def _show_in_terminal(code: str, to_addr: str) -> None:
    banner = (
        "\n"
        "╭─────── KYA-Broker DEV mode OTP ───────╮\n"
        f"│  Would email to: {to_addr:<20}│\n"
        f"│  Code: {code:<29}│\n"
        "│  (set SMTP env vars to send for real) │\n"
        "╰───────────────────────────────────────╯\n"
    )
    print(banner, flush=True)


@dataclass
class EmailOtpOutcome:
    verified: bool
    reason: str                        # "ok" | "code_mismatch" | "declined" | "timeout" | "error"
    attempts: int = 1
    code_was_sent: bool = False


class EmailOtpVerifier:
    """Sends a broker-issued OTP to the locked email and collects it via popup."""

    def __init__(
        self,
        popup: PopupServer | None = None,
        smtp: SmtpConfig | None = None,
        code_generator: Callable[[], str] | None = None,
    ) -> None:
        self._popup = popup
        self._smtp = smtp if smtp is not None else SmtpConfig.from_env()
        self._generate = code_generator or _generate_code

    async def verify(
        self,
        *,
        intent_id: str,
        amount_usd: float,
        merchant: str,
        timeout_seconds: int = 300,
        popup: PopupServer | None = None,
    ) -> EmailOtpOutcome:
        popup = popup or self._popup
        if popup is None:
            from .popup_server import shared_popup_server

            popup = shared_popup_server()

        try:
            to_addr = load_locked_email()
        except EmailLockError as e:
            return EmailOtpOutcome(verified=False, reason=f"email_lock_error: {e}")
        if to_addr is None:
            return EmailOtpOutcome(
                verified=False,
                reason="no_locked_email — run `broker email-lock <addr>` first",
            )

        code = self._generate()
        dev_mode = os.environ.get("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "").lower() in {
            "1",
            "true",
            "yes",
        }
        sent = False
        if dev_mode or self._smtp is None:
            _show_in_terminal(code, to_addr)
            if not dev_mode:
                logger.warning(
                    "SMTP not configured — falling back to terminal display. "
                    "This is insecure; set KYA_BROKER_SMTP_* in .env for production."
                )
        else:
            try:
                msg = _render_email(code, amount_usd, merchant, intent_id)
                await asyncio.to_thread(_send_email, msg, to_addr, self._smtp)
                sent = True
            except Exception as e:  # noqa: BLE001
                logger.exception("email send failed")
                return EmailOtpOutcome(
                    verified=False, reason=f"smtp_error: {type(e).__name__}: {e}"
                )

        # Collect code via popup
        session = popup.create_session(
            title=f"Confirm ${amount_usd:.2f} at {merchant}",
            instruction=(
                f"We sent a 6-digit confirmation code to your locked email "
                f"({to_addr}). Enter it below to authorise a ${amount_usd:.2f} "
                f"charge at {merchant}. This step is the broker's own safety "
                f"floor — the merchant's checkout (card / wallet / magic link) "
                f"will follow after you confirm here."
                if sent
                else f"DEV MODE — we displayed the code in the terminal. Paste it here."
            ),
            fields=[
                PopupField(
                    key="code",
                    label="6-digit code",
                    type=FieldType.OTP,
                    placeholder="123456",
                    max_length=6,
                )
            ],
            timeout_seconds=timeout_seconds,
        )
        result = await popup.wait_for_submission(session)

        if result.outcome == PopupOutcome.DECLINED:
            return EmailOtpOutcome(
                verified=False, reason="declined", code_was_sent=sent
            )
        if result.outcome == PopupOutcome.TIMEOUT:
            return EmailOtpOutcome(
                verified=False, reason="timeout", code_was_sent=sent
            )
        if result.outcome == PopupOutcome.CANCELLED:
            return EmailOtpOutcome(
                verified=False, reason="cancelled", code_was_sent=sent
            )

        entered = (result.data.get("code") or "").strip()
        if entered == code:
            return EmailOtpOutcome(verified=True, reason="ok", code_was_sent=sent)
        return EmailOtpOutcome(
            verified=False,
            reason=f"code_mismatch (entered {entered[:3]}..., expected {code[:3]}...)",
            code_was_sent=sent,
        )
