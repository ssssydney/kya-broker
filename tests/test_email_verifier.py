"""Email OTP verifier tests.

No real SMTP — we inject a popup, stub the code generator, and assert the
verifier's decision logic for each outcome branch.
"""

from __future__ import annotations

import asyncio

import pytest

from src.email_lock import lock_email
from src.email_verifier import EmailOtpVerifier
from src.popup_server import (
    FieldType,
    PopupField,
    PopupOutcome,
    PopupResult,
    PopupServer,
    PopupSession,
)


class _StubPopup(PopupServer):
    """Popup stub that returns a pre-set result instead of starting HTTP."""

    def __init__(self, canned: PopupResult) -> None:
        super().__init__()
        self.canned = canned
        self.last_session: PopupSession | None = None

    def ensure_started(self) -> None:
        pass  # no real server

    def create_session(self, *, title, instruction, fields, timeout_seconds=300, open_browser=True):
        import secrets

        session = PopupSession(
            session_id=secrets.token_urlsafe(16),
            title=title,
            instruction_html=instruction,
            fields=list(fields),
            timeout_seconds=timeout_seconds,
            created_at_ms=0,
        )
        self.last_session = session
        return session

    async def wait_for_submission(self, session):
        return self.canned


@pytest.mark.asyncio
async def test_verify_ok_when_code_matches(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "1")
    lock_email("warren@example.com")

    popup = _StubPopup(
        PopupResult(outcome=PopupOutcome.SUBMITTED, data={"code": "424242"})
    )
    verifier = EmailOtpVerifier(popup=popup, code_generator=lambda: "424242")
    outcome = await verifier.verify(
        intent_id="i1", amount_usd=5.0, merchant="openrouter.ai", timeout_seconds=5
    )
    assert outcome.verified is True
    assert outcome.reason == "ok"


@pytest.mark.asyncio
async def test_verify_rejects_wrong_code(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "1")
    lock_email("warren@example.com")

    popup = _StubPopup(
        PopupResult(outcome=PopupOutcome.SUBMITTED, data={"code": "000000"})
    )
    verifier = EmailOtpVerifier(popup=popup, code_generator=lambda: "424242")
    outcome = await verifier.verify(
        intent_id="i1", amount_usd=5.0, merchant="openrouter.ai", timeout_seconds=5
    )
    assert outcome.verified is False
    assert "code_mismatch" in outcome.reason


@pytest.mark.asyncio
async def test_verify_declined(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "1")
    lock_email("warren@example.com")

    popup = _StubPopup(PopupResult(outcome=PopupOutcome.DECLINED))
    verifier = EmailOtpVerifier(popup=popup, code_generator=lambda: "424242")
    outcome = await verifier.verify(
        intent_id="i1", amount_usd=5.0, merchant="openrouter.ai", timeout_seconds=5
    )
    assert outcome.verified is False
    assert outcome.reason == "declined"


@pytest.mark.asyncio
async def test_verify_timeout(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "1")
    lock_email("warren@example.com")

    popup = _StubPopup(PopupResult(outcome=PopupOutcome.TIMEOUT))
    verifier = EmailOtpVerifier(popup=popup, code_generator=lambda: "424242")
    outcome = await verifier.verify(
        intent_id="i1", amount_usd=5.0, merchant="openrouter.ai", timeout_seconds=5
    )
    assert outcome.verified is False
    assert outcome.reason == "timeout"


@pytest.mark.asyncio
async def test_verify_refuses_when_no_email_locked(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "1")
    # No lock_email call — email is not locked

    popup = _StubPopup(PopupResult(outcome=PopupOutcome.SUBMITTED, data={"code": "x"}))
    verifier = EmailOtpVerifier(popup=popup, code_generator=lambda: "x")
    outcome = await verifier.verify(
        intent_id="i1", amount_usd=5.0, merchant="openrouter.ai", timeout_seconds=5
    )
    assert outcome.verified is False
    assert "no_locked_email" in outcome.reason
