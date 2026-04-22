"""Dry-run auditor. Used for smoke tests and the setup wizard's end-to-end check
when the user hasn't yet configured real API keys.

Activation:
    KYA_BROKER_DRY_RUN_AUDITOR=approve | reject

Only available when the env var is explicitly set — never picked up in
production by accident.
"""

from __future__ import annotations

import os

from ..intent import Intent
from .base import Auditor, AuditContext, AuditResult, Verdict


class MockAuditor(Auditor):
    def __init__(self) -> None:
        self._verdict = os.environ.get("KYA_BROKER_DRY_RUN_AUDITOR", "approve").lower()

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return "mock-auditor"

    def is_available(self) -> bool:
        return bool(os.environ.get("KYA_BROKER_DRY_RUN_AUDITOR"))

    async def audit(
        self, intent: Intent, context: AuditContext, timeout_seconds: int
    ) -> AuditResult:
        verdict = "approve" if self._verdict == "approve" else "reject"
        return AuditResult(
            auditor_name=self.name,
            verdict=Verdict(
                intent_id=intent.intent_id,
                verdict=verdict,
                concerns=[f"mock auditor ({self._verdict}) — do not use in production"],
            ),
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            raw_output=f'{{"verdict":"{verdict}"}}',
            model=self.model,
        )
