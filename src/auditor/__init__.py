"""Dual auditor layer.

Design intent:
  * Primary auditor decides whether a payment goes through.
  * Shadow auditor (when configured) runs in parallel but only writes to ledger —
    useful for A/B research comparing Codex vs Claude verdicts.
  * Both auditors conform to the same Auditor ABC so they're substitutable.
  * Codex is preferred because it's a cross-model-family auditor for a Claude
    Code agent — using the same model family for audit shares training biases.
"""

from __future__ import annotations

from .base import (
    AuditContext,
    Auditor,
    AuditResult,
    AuditorUnavailableError,
    Verdict,
)
from .claude import ClaudeAuditor
from .codex import CodexAuditor
from .runner import AuditRunner, select_auditor

__all__ = [
    "AuditContext",
    "Auditor",
    "AuditResult",
    "AuditRunner",
    "AuditorUnavailableError",
    "ClaudeAuditor",
    "CodexAuditor",
    "Verdict",
    "select_auditor",
]
