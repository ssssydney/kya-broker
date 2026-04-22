"""Auditor JSON-parsing and selection tests (no live API calls)."""

from __future__ import annotations

import pytest

from src.auditor.base import Verdict, parse_verdict_json
from src.auditor.runner import AuditSetupError, select_auditor
from src.config import Config


def test_parse_raw_json():
    raw = '{"intent_id":"abc","verdict":"approve","concerns":["ok"]}'
    v = parse_verdict_json(raw, "abc")
    assert v.is_approved()
    assert v.concerns == ["ok"]


def test_parse_json_with_markdown_fences():
    raw = '```json\n{"intent_id":"abc","verdict":"reject","concerns":["scale"]}\n```'
    v = parse_verdict_json(raw, "abc")
    assert v.verdict == "reject"


def test_parse_json_with_preamble():
    raw = 'Let me think... {"intent_id":"abc","verdict":"approve","concerns":["fine"]}'
    v = parse_verdict_json(raw, "abc")
    assert v.verdict == "approve"


def test_parse_garbage_rejects():
    v = parse_verdict_json("no json at all here", "fallback-id")
    assert v.verdict == "reject"
    assert v.intent_id == "fallback-id"


def test_parse_invalid_verdict_value_becomes_reject():
    raw = '{"intent_id":"abc","verdict":"maybe","concerns":[]}'
    v = parse_verdict_json(raw, "abc")
    assert v.verdict == "reject"


def test_parse_recommended_amount_numeric():
    raw = '{"intent_id":"a","verdict":"approve","concerns":[],"recommended_amount_usd":"7.50"}'
    v = parse_verdict_json(raw, "a")
    assert v.recommended_amount_usd == 7.5


def test_select_auditor_codex_required_but_missing():
    cfg = Config()
    cfg.audit.primary = "codex"
    with pytest.raises(AuditSetupError):
        # No codex CLI, no OPENAI_API_KEY → must fail
        select_auditor(cfg)


def test_select_auditor_claude_required_but_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    cfg.audit.primary = "claude"
    with pytest.raises(AuditSetupError):
        select_auditor(cfg)


def test_verdict_to_dict_round_trip():
    v = Verdict(intent_id="abc", verdict="approve", concerns=["x", "y"], recommended_amount_usd=5)
    d = v.to_dict()
    assert d["intent_id"] == "abc"
    assert d["verdict"] == "approve"
    assert d["concerns"] == ["x", "y"]
    assert d["recommended_amount_usd"] == 5
