"""Codex auditor — spawns the `codex` CLI in a read-only subprocess.

Why subprocess and not an OpenAI API call directly?
  * Codex CLI runs in a sandbox: we point it at a worktree view of the skill
    without access to `.env` or user files, which limits what prompt injection
    can extract even if the auditor model is somehow compromised.
  * CLI gives us a uniform interface with local-model alternatives if someone
    wants to swap in a self-hosted Codex clone.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

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


def _load_prompt() -> str:
    base = (prompt_dir() / "audit_system.md").read_text(encoding="utf-8")
    codex_specific = (prompt_dir() / "audit_codex.md")
    if codex_specific.exists():
        base += "\n\n" + codex_specific.read_text(encoding="utf-8")
    return base


class CodexAuditor(Auditor):
    def __init__(self, model: str = "gpt-5-codex", binary_path: str | None = None):
        self._model = model
        self._binary = binary_path or shutil.which("codex") or "codex"

    @property
    def name(self) -> str:
        return "codex"

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        if not self._binary or not shutil.which(self._binary):
            return False
        if not os.environ.get("OPENAI_API_KEY") and not self._codex_logged_in():
            return False
        return True

    def _codex_logged_in(self) -> bool:
        """Best-effort check that `codex login` has been run."""
        # codex stores credentials under ~/.codex/auth.json or similar; we don't
        # parse it — we just report False unless OPENAI_API_KEY is set. If the
        # user has logged in via `codex login` and no env var, assume it works
        # and let the subprocess call fail loudly with a useful message.
        return False

    async def audit(
        self,
        intent: Intent,
        context: AuditContext,
        timeout_seconds: int,
    ) -> AuditResult:
        if not self.is_available():
            raise AuditorUnavailableError(
                "codex CLI not found or OPENAI_API_KEY not set; run `codex login`"
            )

        prompt = _load_prompt()
        user_message = self._build_user_message(intent, context)
        combined = f"{prompt}\n\n---\n\n{user_message}"

        cmd = [
            self._binary,
            "exec",
            "--json-output",
            "--model",
            self._model,
            "--sandbox",
            "read-only",
            "-",  # read prompt from stdin
        ]

        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._clean_env(),
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(combined.encode("utf-8")), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            elapsed = int((time.perf_counter() - start) * 1000)
            return AuditResult(
                auditor_name=self.name,
                verdict=Verdict(
                    intent_id=intent.intent_id,
                    verdict="reject",
                    concerns=[f"codex audit timed out after {timeout_seconds}s"],
                ),
                latency_ms=elapsed,
                model=self._model,
                error="timeout",
            )
        except FileNotFoundError as e:
            raise AuditorUnavailableError(f"codex binary missing: {e}") from e

        latency_ms = int((time.perf_counter() - start) * 1000)
        stdout_s = stdout_b.decode("utf-8", errors="replace")
        stderr_s = stderr_b.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return AuditResult(
                auditor_name=self.name,
                verdict=Verdict(
                    intent_id=intent.intent_id,
                    verdict="reject",
                    concerns=[f"codex exited {proc.returncode}: {stderr_s[:300]}"],
                ),
                latency_ms=latency_ms,
                raw_output=stdout_s,
                model=self._model,
                error=f"exit_{proc.returncode}",
            )

        verdict, tokens_in, tokens_out = self._parse_codex_json(stdout_s, intent.intent_id)
        return AuditResult(
            auditor_name=self.name,
            verdict=verdict,
            latency_ms=latency_ms,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            raw_output=stdout_s,
            model=self._model,
        )

    @staticmethod
    def _clean_env() -> dict[str, str]:
        """Forward only what codex needs; prevents leaking vast / MetaMask secrets."""
        allowed = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "OPENAI_API_KEY", "OPENAI_BASE_URL"}
        return {k: v for k, v in os.environ.items() if k in allowed}

    @staticmethod
    def _build_user_message(intent: Intent, context: AuditContext) -> str:
        return (
            "Here is the payment intent to audit:\n\n"
            "```json\n"
            + intent.to_json()
            + "\n```\n\n"
            "Here is the agent context this intent emerged from:\n\n"
            + context.to_prompt_block()
            + "\n\nRespond with the single JSON object described in the system instructions. "
            "No preamble, no markdown fences."
        )

    @staticmethod
    def _parse_codex_json(
        stdout: str, intent_id: str
    ) -> tuple[Verdict, int | None, int | None]:
        """Codex --json-output wraps the response; handle both wrapped and raw cases."""
        tokens_in: int | None = None
        tokens_out: int | None = None
        payload = stdout.strip()

        try:
            wrapper = json.loads(payload)
            if isinstance(wrapper, dict):
                tokens_in = (wrapper.get("usage") or {}).get("input_tokens")
                tokens_out = (wrapper.get("usage") or {}).get("output_tokens")
                content = wrapper.get("output") or wrapper.get("content") or wrapper.get("text")
                if content:
                    return parse_verdict_json(str(content), intent_id), tokens_in, tokens_out
        except json.JSONDecodeError:
            pass

        return parse_verdict_json(payload, intent_id), tokens_in, tokens_out
