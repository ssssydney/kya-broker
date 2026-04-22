"""Rail selection tests."""

from __future__ import annotations

import pytest

from src.config import Config, MerchantConfig
from src.intent import Intent
from src.rail_selector import RailUnavailableError, list_rails, select_rail


def _cfg() -> Config:
    c = Config()
    c.rails = ["crypto"]
    c.merchants = [MerchantConfig(name="vast.ai", playbook="vast_topup_crypto.yaml")]
    return c


def _intent(amount: float = 5.0, merchant: str = "vast.ai") -> Intent:
    return Intent(
        merchant=merchant,
        amount_usd=amount,
        rationale="long enough rationale for validation to succeed in tests",
        estimated_actual_cost_usd=amount * 0.9,
    )


def test_list_rails_reports_crypto_available():
    cfg = _cfg()
    rails = list_rails(cfg)
    by_name = {r.name: r for r in rails}
    assert by_name["crypto"].available is True


def test_list_rails_reports_fiat_unavailable():
    cfg = _cfg()
    cfg.rails = ["crypto", "fiat_card"]
    rails = list_rails(cfg)
    fiat = next(r for r in rails if r.name == "fiat_card")
    assert fiat.available is False
    assert "v0.4" in (fiat.reason or "")


def test_select_rail_happy():
    cfg = _cfg()
    rail = select_rail(cfg, _intent(5.0))
    assert rail.name == "crypto"
    assert rail.playbook == "vast_topup_crypto.yaml"


def test_select_rail_respects_merchant_cap():
    cfg = _cfg()
    cfg.merchants[0].max_single_topup_usd = 5.0
    with pytest.raises(RailUnavailableError):
        select_rail(cfg, _intent(amount=10.0))


def test_select_rail_no_rails_configured():
    cfg = _cfg()
    cfg.rails = []
    with pytest.raises(RailUnavailableError):
        select_rail(cfg, _intent(5.0))
