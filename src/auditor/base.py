"""Auditor abstract base class and shared data types."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..intent import Intent


class AuditorUnavailableError(Exception):
    """Raised when an auditor's prerequisites aren't satisfied (missing key, CLI, etc.)."""


@dataclass
class AuditContext:
    """Context passed alongside the intent to the auditor.

    The context is a compressed view of what the calling Claude Code session has
    been talking about. The auditor cross-checks that the intent's `rationale`
    is consistent with this context — a jailbreak that convinces Claude Code to
    overspend will usually leave a trail of mismatch between context and intent.
    """

    conversation_excerpt: str = ""
    cited_files: list[dict[str, str]] = field(default_factory=list)
    # cited_files is a list of {"path": "...", "content_excerpt": "..."} — the
    # broker trims large files so the auditor sees only relevant chunks.

    def to_prompt_block(self) -> str:
        parts: list[str] = []
        if self.conversation_excerpt:
            parts.append("### Agent conversation excerpt\n" + self.conversation_excerpt)
        for f in self.cited_files:
            p = f.get("path", "<unknown>")
            c = f.get("content_excerpt", "")
            parts.append(f"### Cited file: {p}\n```\n{c}\n```")
        if not parts:
            return "(No context provided.)"
        return "\n\n".join(parts)


@dataclass
class Verdict:
    intent_id: str
    verdict: str  # "approve" | "reject"
    concerns: list[str] = field(default_factory=list)
    recommended_amount_usd: float | None = None

    def is_approved(self) -> bool:
        return self.verdict == "approve"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "verdict": self.verdict,
            "concerns": self.concerns,
            "recommended_amount_usd": self.recommended_amount_usd,
        }


@dataclass
class AuditResult:
    """Full result of one auditor run, including instrumentation for the ledger."""

    auditor_name: str
    verdict: Verdict
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_output: str | None = None
    model: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def parse_verdict_json(raw: str, fallback_intent_id: str) -> Verdict:
    """Robustly parse an auditor's JSON output.

    Strategy:
      1. Try raw JSON parse.
      2. If that fails, strip markdown code fences and retry.
      3. If that fails, extract the first top-level JSON object heuristically.
      4. If everything fails, construct a reject verdict citing the parse error.
    """
    cleaned = raw.strip()

    # remove markdown fences
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return Verdict(
                    intent_id=fallback_intent_id,
                    verdict="reject",
                    concerns=[f"auditor output was not valid JSON: {cleaned[:200]}"],
                )
        else:
            return Verdict(
                intent_id=fallback_intent_id,
                verdict="reject",
                concerns=[f"auditor output was not valid JSON: {cleaned[:200]}"],
            )

    verdict_str = str(data.get("verdict", "reject")).lower()
    if verdict_str not in ("approve", "reject"):
        verdict_str = "reject"

    concerns = data.get("concerns") or []
    if not isinstance(concerns, list):
        concerns = [str(concerns)]
    concerns = [str(c) for c in concerns]

    rec = data.get("recommended_amount_usd")
    if rec is not None:
        try:
            rec = float(rec)
        except (TypeError, ValueError):
            rec = None

    return Verdict(
        intent_id=str(data.get("intent_id") or fallback_intent_id),
        verdict=verdict_str,
        concerns=concerns,
        recommended_amount_usd=rec,
    )


class Auditor(ABC):
    """Base class all concrete auditors inherit from."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    async def audit(
        self,
        intent: Intent,
        context: AuditContext,
        timeout_seconds: int,
    ) -> AuditResult: ...
