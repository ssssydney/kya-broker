"""Shared pytest fixtures. Redirects KYA_BROKER_LOCAL so the real user state is untouched."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point the skill at a tmpdir for every test."""
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("KYA_BROKER_LOCAL", str(local))

    # Seed default policy into the isolated state dir
    repo_root = Path(__file__).resolve().parent.parent
    src_policy = repo_root / "policy.default.yaml"
    dst_policy = local / "config.yaml"
    shutil.copyfile(src_policy, dst_policy)

    # Point skill_root at repo for prompt + playbook resolution
    monkeypatch.setenv("KYA_BROKER_HOME", str(repo_root))

    # Force dry-run for chrome
    monkeypatch.setenv("KYA_BROKER_DRY_RUN", "1")
    monkeypatch.setenv("KYA_BROKER_DRY_RUN_OUTCOME", "settled")

    yield local


@pytest.fixture
def sample_intent_payload() -> dict:
    return {
        "merchant": "vast.ai",
        "amount_usd": 1.50,
        "rationale": "Attention-sink ablation on 1x RTX 4090 per paper's Table 3 configuration",
        "estimated_actual_cost_usd": 1.20,
        "references": ["papers/attention-sink.pdf"],
    }
