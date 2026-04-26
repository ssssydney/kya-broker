"""Shared pytest fixtures. Redirects KYA_BROKER_LOCAL so the real ledger is untouched."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("KYA_BROKER_LOCAL", str(local))
    yield local
