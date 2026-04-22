"""Audit runner: selects auditors per config and orchestrates primary + shadow runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..config import Config
from ..intent import Intent
from ..ledger import Ledger
from .base import AuditContext, Auditor, AuditResult, AuditorUnavailableError, Verdict
from .claude import ClaudeAuditor
from .codex import CodexAuditor
from .mock import MockAuditor


class AuditSetupError(Exception):
    pass


def _build_auditors(cfg: Config) -> tuple[CodexAuditor, ClaudeAuditor]:
    codex = CodexAuditor(
        model=cfg.audit.codex.model,
        binary_path=cfg.audit.codex.binary_path,
    )
    claude = ClaudeAuditor(
        model=cfg.audit.claude.model,
        max_output_tokens=cfg.audit.claude.max_output_tokens,
    )
    return codex, claude


def select_auditor(cfg: Config) -> tuple[Auditor, list[Auditor]]:
    """Return (primary, shadow_list) per cfg.audit settings.

    primary = codex | claude | auto
    shadow  = [the other one] iff cfg.audit.shadow_mode and both are available
    """
    # Explicit mock override — only honored when the env var is set, so it
    # can't accidentally be picked up in production.
    mock = MockAuditor()
    if mock.is_available():
        return mock, []

    codex, claude = _build_auditors(cfg)

    mode = cfg.audit.primary
    if mode == "auto":
        primary: Auditor = codex if codex.is_available() else claude
    elif mode == "codex":
        if not codex.is_available():
            raise AuditSetupError(
                "audit.primary=codex but codex is not available (install codex CLI and log in)"
            )
        primary = codex
    elif mode == "claude":
        if not claude.is_available():
            raise AuditSetupError(
                "audit.primary=claude but ANTHROPIC_API_KEY is not set"
            )
        primary = claude
    else:
        raise AuditSetupError(f"unknown audit.primary={mode!r}")

    if not primary.is_available():
        raise AuditSetupError(
            f"selected primary auditor {primary.name} is not available — check setup"
        )

    shadow: list[Auditor] = []
    if cfg.audit.shadow_mode:
        for candidate in (codex, claude):
            if candidate.name != primary.name and candidate.is_available():
                shadow.append(candidate)

    return primary, shadow


@dataclass
class AuditRunOutcome:
    primary: AuditResult
    shadow: list[AuditResult]

    @property
    def primary_verdict(self) -> Verdict:
        return self.primary.verdict


class AuditRunner:
    def __init__(self, cfg: Config, ledger: Ledger):
        self.cfg = cfg
        self.ledger = ledger

    async def run(self, intent: Intent, context: AuditContext) -> AuditRunOutcome:
        primary, shadow = select_auditor(self.cfg)
        timeout = self.cfg.audit.timeout_seconds

        # Launch primary and shadow concurrently
        primary_task = asyncio.create_task(primary.audit(intent, context, timeout))
        shadow_tasks = [
            asyncio.create_task(s.audit(intent, context, timeout)) for s in shadow
        ]

        primary_result = await primary_task
        shadow_results = (
            list(await asyncio.gather(*shadow_tasks, return_exceptions=True))
            if shadow_tasks
            else []
        )

        # If primary hard-failed and fallback is enabled, try the shadow as fallback
        if (
            primary_result.error
            and self.cfg.audit.fallback_on_primary_failure
            and shadow_results
        ):
            for sr in shadow_results:
                if isinstance(sr, AuditResult) and sr.succeeded:
                    # Promote shadow to primary for this intent
                    self._record(intent.intent_id, primary_result, is_primary=False)
                    self._record(intent.intent_id, sr, is_primary=True)
                    # Record any remaining shadows
                    for other in shadow_results:
                        if isinstance(other, AuditResult) and other is not sr:
                            self._record(intent.intent_id, other, is_primary=False)
                    return AuditRunOutcome(primary=sr, shadow=[primary_result])

        # Normal path
        self._record(intent.intent_id, primary_result, is_primary=True)
        clean_shadow: list[AuditResult] = []
        for sr in shadow_results:
            if isinstance(sr, AuditResult):
                self._record(intent.intent_id, sr, is_primary=False)
                clean_shadow.append(sr)
            else:
                # Unexpected exception in shadow — log a synthetic AuditResult so the
                # ledger still captures the event without breaking the main flow.
                err_result = AuditResult(
                    auditor_name="shadow_exception",
                    verdict=Verdict(
                        intent_id=intent.intent_id,
                        verdict="reject",
                        concerns=[f"shadow auditor crashed: {sr!r}"],
                    ),
                    latency_ms=0,
                    error=repr(sr),
                )
                self._record(intent.intent_id, err_result, is_primary=False)
                clean_shadow.append(err_result)

        return AuditRunOutcome(primary=primary_result, shadow=clean_shadow)

    def _record(self, intent_id: str, result: AuditResult, is_primary: bool) -> None:
        raw = result.raw_output if self.cfg.observability.retain_raw_audit_output else None
        self.ledger.record_audit(
            intent_id=intent_id,
            auditor_name=result.auditor_name,
            is_primary=is_primary,
            verdict=result.verdict.verdict,
            concerns=result.verdict.concerns,
            recommended_amount_usd=result.verdict.recommended_amount_usd,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            raw_output=raw,
            model=result.model,
        )
