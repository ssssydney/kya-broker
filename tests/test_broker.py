"""End-to-end broker tests with a stubbed auditor (no live API).

The broker is wired against the dry-run Chrome bridge via the conftest env var,
so these tests exercise: validation, tier classification, merchant allowlist,
daily/monthly caps, audit invocation (stubbed), state machine, and execution
recording in ledger.
"""

from __future__ import annotations

import asyncio

import pytest

from src.auditor import AuditContext
from src.auditor.base import AuditResult, Auditor, Verdict
from src.auditor.runner import AuditRunner
from src.broker import Broker, BrokerError
from src.config import load_config
from src.intent import IntentState
from src.ledger import Ledger


class StubAuditor(Auditor):
    """Always-approve auditor used in tests."""

    def __init__(self, verdict: str = "approve", concerns=None):
        self._verdict = verdict
        self._concerns = concerns or ["stub auditor approval"]

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str:
        return "stub-model"

    def is_available(self) -> bool:
        return True

    async def audit(self, intent, context, timeout_seconds):
        return AuditResult(
            auditor_name="stub",
            verdict=Verdict(
                intent_id=intent.intent_id,
                verdict=self._verdict,
                concerns=self._concerns,
            ),
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            raw_output="stub",
            model=self.model,
        )


def _broker_with_stub(auditor: Auditor) -> Broker:
    cfg = load_config()
    ledger = Ledger()
    broker = Broker(config=cfg, ledger=ledger)

    class StubRunner(AuditRunner):
        async def run(self, intent, context):
            result = await auditor.audit(intent, context, cfg.audit.timeout_seconds)
            self._record(intent.intent_id, result, is_primary=True)
            from src.auditor.runner import AuditRunOutcome

            return AuditRunOutcome(primary=result, shadow=[])

    broker.audit = StubRunner(cfg, ledger)
    return broker


@pytest.mark.asyncio
async def test_l0_auto_executes(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("approve"))
    resp = await broker.propose_intent(
        {**sample_intent_payload, "amount_usd": 1.0, "estimated_actual_cost_usd": 0.9},
        AuditContext(conversation_excerpt="user wants small ablation on 4090"),
    )
    assert resp.state == IntentState.SETTLED.value
    assert resp.tier == "L0"


@pytest.mark.asyncio
async def test_l1_waits_for_user(sample_intent_payload, monkeypatch):
    # l1 threshold is 50; 10 is L1 in default config
    # use outcome 'user_declined' so the re-run after resume fails cleanly — but
    # we first assert that without resume we only get awaiting_user.
    broker = _broker_with_stub(StubAuditor("approve"))

    # Prevent auto-progression by forcing L1 amount
    payload = {**sample_intent_payload, "amount_usd": 10.0, "estimated_actual_cost_usd": 9.0}
    resp = await broker.propose_intent(payload, AuditContext(conversation_excerpt="x" * 50))
    assert resp.state == IntentState.AWAITING_USER.value
    assert resp.tier == "L1"

    # Now resume
    resp2 = await broker.resume_awaiting_user(resp.intent_id)
    assert resp2.state == IntentState.SETTLED.value


@pytest.mark.asyncio
async def test_l2_rejected_without_user(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("approve"))
    resp = await broker.propose_intent(
        {**sample_intent_payload, "amount_usd": 1000.0, "estimated_actual_cost_usd": 900.0},
        AuditContext(),
    )
    assert resp.state == IntentState.REJECTED.value
    assert resp.tier == "L2"


@pytest.mark.asyncio
async def test_auditor_reject_halts(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("reject", concerns=["scale_mismatch"]))
    resp = await broker.propose_intent(sample_intent_payload, AuditContext())
    assert resp.state == IntentState.REJECTED.value
    assert "scale_mismatch" in (resp.concerns or [])


@pytest.mark.asyncio
async def test_unknown_merchant_rejected(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("approve"))
    payload = {**sample_intent_payload, "merchant": "sketchy-merchant.example.com"}
    resp = await broker.propose_intent(payload, AuditContext())
    assert resp.state == IntentState.REJECTED.value


@pytest.mark.asyncio
async def test_daily_cap_enforced(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("approve"))
    broker.cfg.thresholds.l0_ceiling_usd = 5.0  # keep both intents in L0 so they auto-execute
    broker.cfg.thresholds.daily_cap_usd = 5.0
    # First 3-dollar intent settles
    p1 = {**sample_intent_payload, "amount_usd": 3.0, "estimated_actual_cost_usd": 2.8}
    r1 = await broker.propose_intent(p1, AuditContext())
    assert r1.state == IntentState.SETTLED.value
    # Second 3-dollar intent would push us over the $5 cap → rejected
    p2 = {**sample_intent_payload, "amount_usd": 3.0, "estimated_actual_cost_usd": 2.8}
    r2 = await broker.propose_intent(p2, AuditContext())
    assert r2.state == IntentState.REJECTED.value
    assert "daily cap" in (r2.message or "")


@pytest.mark.asyncio
async def test_invalid_intent_raises(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("approve"))
    with pytest.raises(BrokerError):
        await broker.propose_intent({**sample_intent_payload, "amount_usd": -1}, AuditContext())


@pytest.mark.asyncio
async def test_user_declined_in_metamask(sample_intent_payload, monkeypatch):
    monkeypatch.setenv("KYA_BROKER_DRY_RUN_OUTCOME", "user_declined")
    broker = _broker_with_stub(StubAuditor("approve"))
    payload = {**sample_intent_payload, "amount_usd": 1.0, "estimated_actual_cost_usd": 0.9}
    resp = await broker.propose_intent(payload, AuditContext())
    assert resp.state == IntentState.USER_DECLINED.value


@pytest.mark.asyncio
async def test_history_reflects_intents(sample_intent_payload):
    broker = _broker_with_stub(StubAuditor("approve"))
    for i in range(3):
        payload = {
            **sample_intent_payload,
            "amount_usd": 1.0 + 0.1 * i,
            "estimated_actual_cost_usd": 0.9,
            "rationale": f"test intent number {i}, long enough for validation",
        }
        await broker.propose_intent(payload, AuditContext())
    rows = broker.history(limit=10)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_check_balance_returns_spend_metrics():
    broker = _broker_with_stub(StubAuditor("approve"))
    bal = broker.check_balance()
    assert "spent_last_24h_usd" in bal
    assert "remaining_today_usd" in bal
    assert "cap_daily_usd" in bal
