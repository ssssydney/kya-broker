"""Tests for the HumanGate primitive.

No live browser needed — predicates are python callables, so we pass stubs.
"""

from __future__ import annotations

import asyncio

import pytest

from src.human_gate import (
    DEFAULT_COMPLETION_KEYWORDS,
    DEFAULT_DECLINE_KEYWORDS,
    HumanGate,
    HumanGateOutcome,
    HumanGateReason,
    HumanGateRequest,
    default_human_prompt,
)


def _always(value: bool):
    async def _fn() -> bool:
        return value

    return _fn


def _delayed(flag: list[bool], delay_s: float):
    """A predicate that returns False until flag[0] flips, simulating user action."""

    async def _fn() -> bool:
        await asyncio.sleep(0)
        return flag[0]

    return _fn


@pytest.mark.asyncio
async def test_dry_run_completed(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_DRY_RUN", "1")
    monkeypatch.setenv("KYA_BROKER_DRY_RUN_HUMAN_GATE", "completed")
    gate = HumanGate()
    result = await gate.wait_for_human(
        HumanGateRequest(
            reason=HumanGateReason.CARD_DETAILS,
            prompt="dry",
            timeout_seconds=1,
        )
    )
    assert result.outcome == HumanGateOutcome.COMPLETED


@pytest.mark.asyncio
async def test_dry_run_declined(monkeypatch):
    monkeypatch.setenv("KYA_BROKER_DRY_RUN", "1")
    monkeypatch.setenv("KYA_BROKER_DRY_RUN_HUMAN_GATE", "declined")
    gate = HumanGate()
    result = await gate.wait_for_human(
        HumanGateRequest(
            reason=HumanGateReason.METAMASK_SIGN,
            prompt="dry",
            timeout_seconds=1,
        )
    )
    assert result.outcome == HumanGateOutcome.DECLINED


@pytest.mark.asyncio
async def test_completes_when_predicate_returns_true(monkeypatch):
    monkeypatch.delenv("KYA_BROKER_DRY_RUN", raising=False)
    flag = [False]
    pred_ok = _delayed(flag, 0)
    gate = HumanGate(poll_interval_s=0.05)

    async def flip():
        await asyncio.sleep(0.1)
        flag[0] = True

    asyncio.create_task(flip())
    result = await gate.wait_for_human(
        HumanGateRequest(
            reason=HumanGateReason.CARD_DETAILS,
            prompt="enter card",
            timeout_seconds=3,
            on_completion=pred_ok,
        )
    )
    assert result.outcome == HumanGateOutcome.COMPLETED


@pytest.mark.asyncio
async def test_declines_short_circuits(monkeypatch):
    monkeypatch.delenv("KYA_BROKER_DRY_RUN", raising=False)
    gate = HumanGate(poll_interval_s=0.05)
    result = await gate.wait_for_human(
        HumanGateRequest(
            reason=HumanGateReason.METAMASK_SIGN,
            prompt="sign",
            timeout_seconds=3,
            on_completion=_always(False),
            on_decline=_always(True),
        )
    )
    assert result.outcome == HumanGateOutcome.DECLINED


@pytest.mark.asyncio
async def test_times_out(monkeypatch):
    monkeypatch.delenv("KYA_BROKER_DRY_RUN", raising=False)
    gate = HumanGate(poll_interval_s=0.05)
    result = await gate.wait_for_human(
        HumanGateRequest(
            reason=HumanGateReason.CARD_3DS,
            prompt="3ds",
            timeout_seconds=1,
            on_completion=_always(False),
        )
    )
    assert result.outcome == HumanGateOutcome.TIMEOUT


@pytest.mark.asyncio
async def test_optional_gate_skips_when_not_present(monkeypatch):
    monkeypatch.delenv("KYA_BROKER_DRY_RUN", raising=False)
    gate = HumanGate(poll_interval_s=0.05)
    result = await gate.wait_for_human(
        HumanGateRequest(
            reason=HumanGateReason.CARD_3DS,
            prompt="3ds",
            timeout_seconds=10,
            optional=True,
            presence_check=_always(False),
            on_completion=_always(True),
        )
    )
    assert result.outcome == HumanGateOutcome.SKIPPED


def test_default_completion_keywords_cover_all_reasons():
    for reason in HumanGateReason:
        assert reason in DEFAULT_COMPLETION_KEYWORDS
        assert DEFAULT_COMPLETION_KEYWORDS[reason], f"{reason} has no completion keywords"


def test_default_decline_keywords_cover_all_reasons():
    for reason in HumanGateReason:
        assert reason in DEFAULT_DECLINE_KEYWORDS


def test_prompt_renders_amount_and_merchant():
    p = default_human_prompt(HumanGateReason.CARD_DETAILS, 12.34, "openrouter.ai")
    assert "openrouter.ai" in p
    assert "$12.34" in p


def test_prompt_per_reason_differs():
    p1 = default_human_prompt(HumanGateReason.CARD_DETAILS, 10.0, "m")
    p2 = default_human_prompt(HumanGateReason.METAMASK_SIGN, 10.0, "m")
    p3 = default_human_prompt(HumanGateReason.EMAIL_MAGIC_LINK, 10.0, "m")
    assert p1 != p2 != p3
