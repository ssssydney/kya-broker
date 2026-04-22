"""Ledger CRUD and aggregation tests."""

from __future__ import annotations

from src.intent import Intent, IntentState
from src.ledger import Ledger


def _intent() -> Intent:
    return Intent(
        merchant="vast.ai",
        amount_usd=5.0,
        rationale="small topup for an attention ablation run on 4090",
        estimated_actual_cost_usd=4.5,
    )


def test_insert_and_read():
    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    row = ledger.get_intent(intent.intent_id)
    assert row is not None
    assert row["current_state"] == IntentState.PROPOSED.value
    assert row["tier"] == "L1"


def test_transition_records_event():
    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    ledger.transition(intent.intent_id, IntentState.AUDITED, reason="test")
    row = ledger.get_intent(intent.intent_id)
    assert row["current_state"] == IntentState.AUDITED.value
    history = ledger.state_history(intent.intent_id)
    # created event + 1 transition event
    assert len(history) == 2
    assert history[-1]["to_state"] == "audited"


def test_invalid_transition_raises():
    import pytest

    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    from src.intent import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        ledger.transition(intent.intent_id, IntentState.SETTLED)


def test_audit_record_and_fetch():
    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    ledger.record_audit(
        intent_id=intent.intent_id,
        auditor_name="codex",
        is_primary=True,
        verdict="approve",
        concerns=["scale looks fine"],
        recommended_amount_usd=None,
        latency_ms=1234,
        input_tokens=100,
        output_tokens=50,
        raw_output='{"verdict":"approve"}',
        model="gpt-5-codex",
    )
    audits = ledger.audits_for(intent.intent_id)
    assert len(audits) == 1
    assert audits[0]["verdict"] == "approve"


def test_execution_tracking():
    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    ledger.start_execution(intent.intent_id, rail="crypto")
    ledger.complete_execution(
        intent.intent_id,
        tx_hash="0xabc",
        merchant_receipt_id="r-123",
        actual_cost_usd=4.75,
    )
    ex = ledger.execution_for(intent.intent_id)
    assert ex["tx_hash"] == "0xabc"
    assert ex["actual_cost_usd"] == 4.75


def test_spending_aggregation():
    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    ledger.transition(intent.intent_id, IntentState.AUDITED)
    ledger.transition(intent.intent_id, IntentState.EXECUTING)
    ledger.transition(intent.intent_id, IntentState.SETTLED)
    ledger.start_execution(intent.intent_id, rail="crypto")
    ledger.complete_execution(intent.intent_id, actual_cost_usd=5.0)
    assert ledger.spent_last_24h() == 5.0


def test_audit_comparison_shape():
    ledger = Ledger()
    intent = _intent()
    ledger.insert_intent(intent, tier="L1")
    ledger.record_audit(
        intent.intent_id, "codex", True, "approve", [], None, 100, 10, 5, None, "m1"
    )
    ledger.record_audit(
        intent.intent_id, "claude", False, "reject", ["differs"], None, 200, 10, 5, None, "m2"
    )
    rows = ledger.audit_comparison()
    assert len(rows) == 1
    assert rows[0]["codex_verdict"] == "approve"
    assert rows[0]["claude_verdict"] == "reject"
