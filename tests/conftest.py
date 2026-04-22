"""Shared pytest fixtures. Redirects KYA_BROKER_LOCAL so the real user state is untouched."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml


def _prepare_local(local: Path, monkeypatch) -> None:
    local.mkdir(exist_ok=True)
    monkeypatch.setenv("KYA_BROKER_LOCAL", str(local))
    repo_root = Path(__file__).resolve().parent.parent
    # Start from the default policy, then enroll test payment methods so that
    # rail selection can succeed in broker-level tests without each test
    # having to patch the config.
    policy = yaml.safe_load((repo_root / "policy.default.yaml").read_text(encoding="utf-8"))
    policy["payment_methods"] = [
        {"name": "test crypto wallet", "rail": "crypto", "wallet_address": "0xtest"},
        {"name": "test card", "rail": "card", "last4": "4242"},
        {"name": "test email link", "rail": "email_link"},
    ]
    (local / "config.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    monkeypatch.setenv("KYA_BROKER_HOME", str(repo_root))
    monkeypatch.setenv("KYA_BROKER_DRY_RUN", "1")
    monkeypatch.setenv("KYA_BROKER_DRY_RUN_OUTCOME", "settled")
    monkeypatch.setenv("KYA_BROKER_DRY_RUN_HUMAN_GATE", "completed")


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    local = tmp_path / "local"
    _prepare_local(local, monkeypatch)
    yield local


@pytest.fixture(autouse=True)
def _auto_isolate(tmp_path, monkeypatch, request):
    if "isolated_state" in request.fixturenames:
        yield
        return
    local = tmp_path / "local"
    _prepare_local(local, monkeypatch)
    yield local


@pytest.fixture
def sample_intent_payload() -> dict:
    return {
        "merchant": "vast.ai",
        "amount_usd": 1.50,
        "rationale": "Attention-sink ablation on 1x RTX 4090 per paper Table 3 configuration",
        "estimated_actual_cost_usd": 1.20,
        "references": ["papers/attention-sink.pdf"],
        "rail_hint": "crypto",
    }


# Remove the duplicate fixture definitions left from the older conftest.



@pytest.fixture
def sample_intent_payload() -> dict:
    return {
        "merchant": "vast.ai",
        "amount_usd": 1.50,
        "rationale": "Attention-sink ablation on 1x RTX 4090 per paper Table 3 configuration",
        "estimated_actual_cost_usd": 1.20,
        "references": ["papers/attention-sink.pdf"],
        "rail_hint": "crypto",
    }
