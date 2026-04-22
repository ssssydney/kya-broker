"""Config round-trip + merchant / playbook shape tests."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from src.config import (
    MerchantConfig,
    PaymentMethod,
    load_config,
    save_config,
)


def test_default_policy_loads(isolated_state):
    cfg = load_config()
    assert cfg.version >= 2
    # Default ships at least one merchant
    assert cfg.merchants
    names = {m.name for m in cfg.merchants}
    assert "openrouter.ai" in names
    assert "vast.ai" in names


def test_default_policy_merchants_have_playbooks(isolated_state):
    cfg = load_config()
    for m in cfg.merchants:
        assert m.playbooks, f"merchant {m.name} has no playbooks"
        for rail, pb in m.playbooks.items():
            assert pb.endswith(".yaml"), f"{m.name}/{rail} playbook {pb!r} not yaml"


def test_config_round_trip_preserves_payment_methods(isolated_state):
    cfg = load_config()
    cfg.payment_methods = [
        PaymentMethod(name="visa", rail="card", last4="4242", notes="work"),
        PaymentMethod(name="mm", rail="crypto", wallet_address="0xabc", notes="polygon"),
    ]
    save_config(cfg)
    cfg2 = load_config()
    assert len(cfg2.payment_methods) == 2
    assert cfg2.payment_methods[0].rail == "card"
    assert cfg2.payment_methods[0].last4 == "4242"
    assert cfg2.payment_methods[1].wallet_address == "0xabc"


def test_config_round_trip_preserves_merchants(isolated_state):
    cfg = load_config()
    original_count = len(cfg.merchants)
    cfg.merchants.append(
        MerchantConfig(
            name="example.com",
            playbooks={"card": "example_topup_card.yaml"},
            preferred_rail="card",
        )
    )
    save_config(cfg)
    cfg2 = load_config()
    assert len(cfg2.merchants) == original_count + 1
    ex = cfg2.merchant("example.com")
    assert ex is not None
    assert ex.playbooks == {"card": "example_topup_card.yaml"}


def test_config_accepts_legacy_playbook_field():
    # v0.3.1 used `playbook:` as a single file. Ensure loader still accepts it.
    # The autouse fixture has already set up KYA_BROKER_LOCAL with a v2 config
    # — we overwrite it with a v1-style config in place.
    local = Path(os.environ["KYA_BROKER_LOCAL"])
    (local / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "merchants": [
                    {
                        "name": "oldmerchant.example",
                        "playbook": "old_style.yaml",
                        "preferred_rail": "crypto",
                    }
                ],
                "rails": ["crypto"],
            }
        )
    )
    cfg = load_config()
    m = cfg.merchant("oldmerchant.example")
    assert m is not None
    assert m.playbooks == {"crypto": "old_style.yaml"}
