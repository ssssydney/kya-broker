"""Claude auditor — independent Anthropic SDK call (NOT the current Claude Code session).

This keeps the auditor's context strictly to {intent, provided_context} with no
agent-session leakage. The auditor model is whatever is set in config, typically
a smaller Sonnet so audit latency stays < 5s.
"""

from __future__ import annotations

import asyncio
import os
import time

from ..intent import Intent
from ..paths import prompt_dir
from .base import (
    Auditor,
    AuditContext,
    AuditResult,
    AuditorUnavailableError,
    Verdict,
    parse_verdict_json,
)


def _load_system_prompt() -> str:
    base = (prompt_dir() / "audit_system.md").read_text(encoding="utf-8")
    claude_specific = prompt_dir() / "audit_claude.md"
    if claude_specific.exists():
        base += "\n\n" + claude_specific.read_text(encoding="utf-8")
    return base


class ClaudeAuditor(Auditor):
    def __init__(self, model: str = "claude-sonnet-4-6", max_output_tokens: int = 2000):
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._client = None

    @property
    def name(self) -> str:
        return "claude"

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    async def audit(
        self,
        intent: Intent,
        context: AuditContext,
        timeout_seconds: int,
    ) -> AuditResult:
        if not self.is_available():
            raise AuditorUnavailableError(
                "Anthropic SDK not installed or ANTHROPIC_API_KEY missing"
            )

        system = _load_system_prompt()
        user_message = (
            "Payment intent:\n\n"
            "```json\n"
            + intent.to_json()
            + "\n```\n\n"
            "Agent context:\n\n"
            + context.to_prompt_block()
            + "\n\nOutput only the JSON verdict described in the system prompt."
        )

        start = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._call_sync, system, user_message),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.perf_counter() - start) * 1000)
            return AuditResult(
                auditor_name=self.name,
                verdict=Verdict(
                    intent_id=intent.intent_id,
                    verdict="reject",
                    concerns=[f"claude audit timed out after {timeout_seconds}s"],
                ),
                latency_ms=elapsed,
                model=self._model,
                error="timeout",
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return AuditResult(
                auditor_name=self.name,
                verdict=Verdict(
                    intent_id=intent.intent_id,
                    verdict="reject",
                    concerns=[f"claude API error: {type(e).__name__}: {e}"],
                ),
                latency_ms=elapsed,
                model=self._model,
                error=str(e),
            )

        latency_ms = int((time.perf_counter() - start) * 1000)

        raw = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        verdict = parse_verdict_json(raw, intent.intent_id)

        return AuditResult(
            auditor_name=self.name,
            verdict=verdict,
            latency_ms=latency_ms,
            input_tokens=getattr(response.usage, "input_tokens", None),
            output_tokens=getattr(response.usage, "output_tokens", None),
            raw_output=raw,
            model=self._model,
        )

    def _call_sync(self, system: str, user_message: str):
        client = self._get_client()
        return client.messages.create(
            model=self._model,
            max_tokens=self._max_output_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
