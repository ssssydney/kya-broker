"""Tests for v1.0 thin ledger."""

from __future__ import annotations

import pytest

from src.ledger import Ledger, LedgerError


def test_log_intent_returns_id():
    led = Ledger()
    intent_id = led.log_intent("vast.ai", 5.0, rationale="paper repro topup")
    assert isinstance(intent_id, str) and len(intent_id) >= 32


def test_log_intent_rejects_negative_amount():
    led = Ledger()
    with pytest.raises(LedgerError):
        led.log_intent("vast.ai", -1.0)


def test_log_intent_rejects_blank_merchant():
    led = Ledger()
    with pytest.raises(LedgerError):
        led.log_intent("   ", 1.0)


def test_log_intent_rejects_invalid_status():
    led = Ledger()
    with pytest.raises(LedgerError):
        led.log_intent("vast.ai", 5.0, status="weird")


def test_get_intent_round_trip():
    led = Ledger()
    intent_id = led.log_intent("openrouter.ai", 10.0, rationale="api top up")
    row = led.get_intent(intent_id)
    assert row is not None
    assert row["merchant"] == "openrouter.ai"
    assert row["amount_usd"] == 10.0
    assert row["status"] == "proposed"


def test_update_intent_status():
    led = Ledger()
    intent_id = led.log_intent("vast.ai", 5.0)
    led.update_intent(intent_id, status="settled", note="receipt 12345")
    row = led.get_intent(intent_id)
    assert row["status"] == "settled"
    assert row["note"] == "receipt 12345"


def test_update_unknown_intent_raises():
    led = Ledger()
    with pytest.raises(LedgerError):
        led.update_intent("not-a-real-id", status="settled")


def test_list_intents_orders_newest_first():
    led = Ledger()
    a = led.log_intent("a.com", 1.0)
    b = led.log_intent("b.com", 2.0)
    c = led.log_intent("c.com", 3.0)
    rows = led.list_intents(limit=10)
    assert [r["intent_id"] for r in rows] == [c, b, a]


def test_budget_set_and_get():
    led = Ledger()
    led.set_budget("daily_cap_usd", 50.0)
    led.set_budget("monthly_cap_usd", 500.0)
    b = led.get_budget()
    assert b["daily_cap_usd"] == 50.0
    assert b["monthly_cap_usd"] == 500.0


def test_budget_rejects_unknown_key():
    led = Ledger()
    with pytest.raises(LedgerError):
        led.set_budget("yearly_cap_usd", 1000.0)


def test_budget_rejects_negative_value():
    led = Ledger()
    with pytest.raises(LedgerError):
        led.set_budget("daily_cap_usd", -1.0)


def test_check_budget_no_caps_set_returns_ok():
    led = Ledger()
    ok, reason = led.check_budget(500.0)
    assert ok is True
    assert reason == "ok"


def test_check_budget_below_caps():
    led = Ledger()
    led.set_budget("daily_cap_usd", 50.0)
    led.set_budget("monthly_cap_usd", 500.0)
    ok, reason = led.check_budget(5.0)
    assert ok is True


def test_check_budget_exceeds_daily():
    led = Ledger()
    led.set_budget("daily_cap_usd", 10.0)
    ok, reason = led.check_budget(15.0)
    assert ok is False
    assert "daily cap" in reason


def test_check_budget_exceeds_monthly_after_settle():
    led = Ledger()
    led.set_budget("daily_cap_usd", 100.0)
    led.set_budget("monthly_cap_usd", 20.0)
    intent_id = led.log_intent("a.com", 15.0)
    led.update_intent(intent_id, status="settled")
    ok, reason = led.check_budget(10.0)
    assert ok is False
    assert "monthly cap" in reason


def test_proposed_intents_dont_count_against_caps():
    led = Ledger()
    led.set_budget("daily_cap_usd", 10.0)
    # Only `proposed` intents — nothing actually settled
    led.log_intent("a.com", 5.0, status="proposed")
    led.log_intent("b.com", 5.0, status="proposed")
    # New $5 intent should still pass; only `settled` counts
    ok, _ = led.check_budget(5.0)
    assert ok is True


def test_export_round_trip():
    led = Ledger()
    led.log_intent("vast.ai", 5.0)
    led.set_budget("daily_cap_usd", 50.0)
    rows = led.list_intents()
    budget = led.get_budget()
    assert len(rows) == 1
    assert budget["daily_cap_usd"] == 50.0
