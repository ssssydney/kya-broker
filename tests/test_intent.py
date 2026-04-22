"""Intent model and state machine tests."""

from __future__ import annotations

import pytest

from src.intent import (
    Intent,
    IntentError,
    IntentState,
    InvalidTransitionError,
    VALID_TRANSITIONS,
    assert_transition,
    authorization_tier,
)


def make_intent(**overrides) -> Intent:
    data = dict(
        merchant="vast.ai",
        amount_usd=10.0,
        rationale="short but long enough rationale for validation",
        estimated_actual_cost_usd=9.0,
    )
    data.update(overrides)
    return Intent(**data)


def test_intent_rejects_nonpositive_amount():
    with pytest.raises(IntentError):
        make_intent(amount_usd=0)


def test_intent_rejects_short_rationale():
    with pytest.raises(IntentError):
        make_intent(rationale="short")


def test_intent_rejects_missing_merchant():
    with pytest.raises(IntentError):
        make_intent(merchant="")


def test_intent_rejects_amount_below_estimate():
    with pytest.raises(IntentError):
        make_intent(amount_usd=1.0, estimated_actual_cost_usd=10.0)


def test_intent_serialises_round_trip():
    intent = make_intent()
    data = intent.to_dict()
    assert data["amount_usd"] == intent.amount_usd
    assert data["merchant"] == intent.merchant
    assert "intent_id" in data
    assert data["created_at"].endswith("Z")


def test_intent_json_is_parseable():
    import json

    intent = make_intent()
    loaded = json.loads(intent.to_json())
    assert loaded["merchant"] == "vast.ai"


@pytest.mark.parametrize(
    "amount,expected",
    [
        (0.50, "L0"),
        (2.00, "L0"),
        (2.01, "L1"),
        (50.00, "L1"),
        (50.01, "L2"),
        (1000.00, "L2"),
    ],
)
def test_tiers(amount, expected):
    assert authorization_tier(amount, 2.0, 50.0) == expected


def test_state_transitions_happy_path():
    assert_transition(IntentState.PROPOSED, IntentState.AUDITED)
    assert_transition(IntentState.AUDITED, IntentState.AWAITING_USER)
    assert_transition(IntentState.AWAITING_USER, IntentState.EXECUTING)
    assert_transition(IntentState.EXECUTING, IntentState.SETTLED)


def test_state_transitions_reject():
    assert_transition(IntentState.PROPOSED, IntentState.REJECTED)


def test_invalid_transition_raises():
    with pytest.raises(InvalidTransitionError):
        assert_transition(IntentState.SETTLED, IntentState.EXECUTING)
    with pytest.raises(InvalidTransitionError):
        assert_transition(IntentState.PROPOSED, IntentState.SETTLED)


def test_terminal_states_have_no_successors():
    for s in (
        IntentState.SETTLED,
        IntentState.FAILED,
        IntentState.USER_DECLINED,
        IntentState.REJECTED,
        IntentState.PLAYBOOK_BROKEN,
    ):
        assert VALID_TRANSITIONS[s] == set()


def test_issuer_session_is_optional():
    intent = make_intent(issuer_session="cc-session-hash-abc")
    assert intent.issuer_session == "cc-session-hash-abc"
    intent2 = make_intent()
    assert intent2.issuer_session is None
