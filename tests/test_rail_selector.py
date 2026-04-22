"""Rail selection tests for the generic v0.4 rails."""

from __future__ import annotations

import pytest

from src.config import Config, MerchantConfig, PaymentMethod
from src.intent import Intent
from src.rail_selector import RailUnavailableError, list_rails, select_rail


def _cfg(
    enrolled_rails: list[str] | None = None,
    merchant_rails: dict[str, str] | None = None,
    merchant_preferred: str = "card",
) -> Config:
    enrolled_rails = enrolled_rails or ["card"]
    merchant_rails = merchant_rails or {"card": "openrouter_topup_card.yaml"}

    c = Config()
    c.rails = ["card", "crypto", "email_link"]
    c.payment_methods = [
        PaymentMethod(name=f"{r} default", rail=r) for r in enrolled_rails
    ]
    c.merchants = [
        MerchantConfig(
            name="openrouter.ai",
            playbooks=merchant_rails,
            preferred_rail=merchant_preferred,
        )
    ]
    return c


def _intent(amount: float = 5.0, rail_hint: str | None = None) -> Intent:
    return Intent(
        merchant="openrouter.ai",
        amount_usd=amount,
        rationale="long enough rationale to pass validation in tests",
        estimated_actual_cost_usd=amount * 0.9,
        rail_hint=rail_hint,
    )


def test_list_rails_reports_enrolled_as_available():
    cfg = _cfg(enrolled_rails=["card"])
    rails = {r.name: r for r in list_rails(cfg)}
    assert rails["card"].available is True
    assert rails["crypto"].available is False


def test_select_rail_card_happy():
    cfg = _cfg(enrolled_rails=["card"])
    rail = select_rail(cfg, _intent())
    assert rail.name == "card"
    assert rail.playbook == "openrouter_topup_card.yaml"


def test_select_rail_crypto_happy():
    cfg = _cfg(
        enrolled_rails=["crypto"],
        merchant_rails={"crypto": "openrouter_topup_crypto.yaml"},
        merchant_preferred="crypto",
    )
    rail = select_rail(cfg, _intent())
    assert rail.name == "crypto"


def test_select_rail_honors_rail_hint():
    cfg = _cfg(
        enrolled_rails=["card", "crypto"],
        merchant_rails={
            "card": "openrouter_topup_card.yaml",
            "crypto": "openrouter_topup_crypto.yaml",
        },
        merchant_preferred="card",
    )
    rail = select_rail(cfg, _intent(rail_hint="crypto"))
    assert rail.name == "crypto"


def test_hint_ignored_if_not_enrolled():
    cfg = _cfg(
        enrolled_rails=["card"],
        merchant_rails={"card": "openrouter_topup_card.yaml"},
    )
    # Hint is crypto but user has no crypto enrolled → fall back to card
    rail = select_rail(cfg, _intent(rail_hint="crypto"))
    assert rail.name == "card"


def test_select_rail_unknown_merchant():
    cfg = _cfg(enrolled_rails=["card"])
    intent = Intent(
        merchant="sketchy-merchant.example.com",
        amount_usd=5.0,
        rationale="long rationale for validation to pass",
        estimated_actual_cost_usd=4.5,
    )
    with pytest.raises(RailUnavailableError) as exc:
        select_rail(cfg, intent)
    assert "not in allowlist" in str(exc.value)


def test_select_rail_merchant_no_playbook_for_enrolled_rail():
    # User has card enrolled but merchant only supports crypto
    cfg = _cfg(
        enrolled_rails=["card"],
        merchant_rails={"crypto": "foo.yaml"},
        merchant_preferred="crypto",
    )
    with pytest.raises(RailUnavailableError) as exc:
        select_rail(cfg, _intent())
    assert "no playbook" in str(exc.value)


def test_select_rail_respects_merchant_cap():
    cfg = _cfg(enrolled_rails=["card"])
    cfg.merchants[0].max_single_topup_usd = 5.0
    with pytest.raises(RailUnavailableError):
        select_rail(cfg, _intent(amount=10.0))


def test_preferred_rail_beats_rails_order():
    cfg = _cfg(
        enrolled_rails=["card", "crypto"],
        merchant_rails={
            "card": "a.yaml",
            "crypto": "b.yaml",
        },
        merchant_preferred="crypto",
    )
    # No hint → merchant preferred_rail (crypto) wins over cfg.rails order
    rail = select_rail(cfg, _intent())
    assert rail.name == "crypto"
