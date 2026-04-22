"""High-level orchestrator — ties intent lifecycle, audit, rail selection, and Chrome together.

Exposed surface:
  * Broker.propose_intent(payload, context) -> BrokerResponse
  * Broker.status(intent_id) -> dict
  * Broker.history(limit) -> list[dict]
  * Broker.check_balance() -> dict
  * Broker.resume_awaiting_user(intent_id) -> BrokerResponse  (used after MetaMask signs)

All methods are async (Chrome + audit I/O is async) but ledger calls are sync
(SQLite is fast enough that wrapping in to_thread would be overkill).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .auditor import AuditContext, AuditRunner, Verdict
from .chrome_bridge import ChromeBridge, ChromeUnavailableError
from .config import Config, load_config
from .intent import (
    DEFAULT_TTL_MINUTES,
    Intent,
    IntentError,
    IntentState,
    authorization_tier,
)
from .ledger import Ledger
from .rail_selector import RailUnavailableError, select_rail


@dataclass
class BrokerResponse:
    intent_id: str
    state: str
    tier: str
    verdict: str | None = None
    concerns: list[str] | None = None
    next_action: str | None = None
    message: str | None = None
    execution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


class BrokerError(Exception):
    pass


class Broker:
    def __init__(
        self,
        config: Config | None = None,
        ledger: Ledger | None = None,
        chrome: ChromeBridge | None = None,
    ):
        self.cfg = config or load_config()
        self.ledger = ledger or Ledger()
        self.chrome = chrome or ChromeBridge(self.cfg.chrome, self.cfg.notifications)
        self.audit = AuditRunner(self.cfg, self.ledger)

    # ---- submission -------------------------------------------------------

    async def propose_intent(
        self,
        payload: dict[str, Any],
        context: AuditContext | None = None,
    ) -> BrokerResponse:
        """Entry point called by Claude Code (via CLI or MCP).

        Handles: validation → classify tier → L2 early-exit → audit → gate → execute.
        """
        context = context or AuditContext()

        # Build + persist intent
        clean = dict(payload)
        clean.pop("intent_id", None)  # broker assigns
        try:
            intent = Intent.from_dict(clean)
        except IntentError as e:
            raise BrokerError(f"invalid intent: {e}") from e

        tier = authorization_tier(
            intent.amount_usd,
            self.cfg.thresholds.l0_ceiling_usd,
            self.cfg.thresholds.l1_ceiling_usd,
        )

        # Refuse upfront for L2: agent should not even send these without user approval.
        if tier == "L2":
            self.ledger.insert_intent(intent, tier)
            self.ledger.transition(
                intent.intent_id,
                IntentState.REJECTED,
                reason="amount exceeds L1 ceiling; requires explicit user approval",
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.REJECTED.value,
                tier=tier,
                message=(
                    f"amount_usd ${intent.amount_usd:.2f} > L1 ceiling "
                    f"${self.cfg.thresholds.l1_ceiling_usd:.2f}. Ask the user first."
                ),
                next_action="user_approval_required",
            )

        # Merchant allowlist
        if self.cfg.merchant(intent.merchant) is None:
            self.ledger.insert_intent(intent, tier)
            self.ledger.transition(
                intent.intent_id,
                IntentState.REJECTED,
                reason=f"merchant {intent.merchant!r} not in allowlist",
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.REJECTED.value,
                tier=tier,
                message=f"merchant {intent.merchant!r} is not in policy.yaml allowlist",
                next_action="user_approval_required",
            )

        # Caps (24h / 30d)
        over_cap = self._check_caps(intent)
        if over_cap is not None:
            self.ledger.insert_intent(intent, tier)
            self.ledger.transition(
                intent.intent_id, IntentState.REJECTED, reason=over_cap
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.REJECTED.value,
                tier=tier,
                message=over_cap,
                next_action="user_approval_required",
            )

        self.ledger.insert_intent(intent, tier)

        # Audit
        outcome = await self.audit.run(intent, context)
        verdict = outcome.primary_verdict

        if not verdict.is_approved():
            self.ledger.transition(
                intent.intent_id,
                IntentState.REJECTED,
                reason="auditor rejected",
                metadata={"concerns": verdict.concerns},
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.REJECTED.value,
                tier=tier,
                verdict=verdict.verdict,
                concerns=verdict.concerns,
                message="auditor rejected intent",
                next_action="revise_and_resubmit",
            )

        self.ledger.transition(
            intent.intent_id,
            IntentState.AUDITED,
            reason="primary auditor approved",
            metadata={"concerns": verdict.concerns},
        )

        # L0 auto-execute; L1 wait for user
        if tier == "L0":
            return await self._execute(intent, tier, verdict)
        else:  # L1
            self.ledger.transition(
                intent.intent_id,
                IntentState.AWAITING_USER,
                reason="L1 intent: human gate will fire during execution",
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.AWAITING_USER.value,
                tier=tier,
                verdict=verdict.verdict,
                concerns=verdict.concerns,
                next_action="wait_for_human_gate",
                message=(
                    "Intent approved by auditor. Call `broker resume <intent_id>` to drive "
                    "Chrome to the merchant's checkout; a human gate will fire (enter card, "
                    "sign in MetaMask, click magic link — depends on the chosen rail). The "
                    "broker will surface that prompt in the terminal when it fires."
                ),
            )

    async def resume_awaiting_user(self, intent_id: str) -> BrokerResponse:
        """Caller has confirmed the user signed in MetaMask; proceed with execution."""
        row = self.ledger.get_intent(intent_id)
        if row is None:
            raise BrokerError(f"no such intent {intent_id}")
        if row["current_state"] != IntentState.AWAITING_USER.value:
            raise BrokerError(
                f"intent is in state {row['current_state']}; cannot resume"
            )

        intent = self._row_to_intent(row)
        tier = row["tier"]
        audits = self.ledger.audits_for(intent_id)
        primary = next((a for a in audits if a["is_primary"]), None)
        verdict = Verdict(
            intent_id=intent_id,
            verdict=primary["verdict"] if primary else "approve",
            concerns=[],
        )
        return await self._execute(intent, tier, verdict)

    # ---- execution --------------------------------------------------------

    async def _execute(
        self, intent: Intent, tier: str, verdict: Verdict
    ) -> BrokerResponse:
        try:
            rail = select_rail(self.cfg, intent)
        except RailUnavailableError as e:
            self.ledger.transition(
                intent.intent_id,
                IntentState.FAILED,
                reason=f"rail_unavailable: {e}",
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.FAILED.value,
                tier=tier,
                message=str(e),
            )

        self.ledger.transition(
            intent.intent_id,
            IntentState.EXECUTING,
            reason=f"rail={rail.name}",
        )
        self.ledger.start_execution(intent.intent_id, rail.name)

        merchant = self.cfg.merchant(intent.merchant)
        assert merchant is not None  # already validated at submit time

        try:
            result = await self.chrome.run_playbook(
                playbook_name=rail.playbook,
                intent=intent,
                merchant=merchant,
            )
        except ChromeUnavailableError as e:
            self.ledger.transition(
                intent.intent_id,
                IntentState.PLAYBOOK_BROKEN,
                reason=f"chrome_unavailable: {e}",
            )
            self.ledger.complete_execution(intent.intent_id, error=str(e))
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.PLAYBOOK_BROKEN.value,
                tier=tier,
                message=str(e),
            )
        except Exception as e:  # noqa: BLE001 — we want any playbook error surfaced
            self.ledger.transition(
                intent.intent_id,
                IntentState.PLAYBOOK_BROKEN,
                reason=f"playbook_exception: {type(e).__name__}: {e}",
            )
            self.ledger.complete_execution(intent.intent_id, error=str(e))
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.PLAYBOOK_BROKEN.value,
                tier=tier,
                message=f"{type(e).__name__}: {e}",
            )

        if result.state == "user_declined":
            self.ledger.transition(
                intent.intent_id,
                IntentState.USER_DECLINED,
                reason="MetaMask popup rejected by user",
            )
            self.ledger.complete_execution(intent.intent_id, error="user_declined")
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.USER_DECLINED.value,
                tier=tier,
                message="user declined in MetaMask",
            )

        if result.state == "settled":
            self.ledger.transition(
                intent.intent_id,
                IntentState.SETTLED,
                reason="merchant confirmed receipt",
                metadata={"tx_hash": result.tx_hash},
            )
            self.ledger.complete_execution(
                intent.intent_id,
                tx_hash=result.tx_hash,
                merchant_receipt_id=result.merchant_receipt_id,
                actual_cost_usd=result.actual_cost_usd,
            )
            return BrokerResponse(
                intent_id=intent.intent_id,
                state=IntentState.SETTLED.value,
                tier=tier,
                verdict=verdict.verdict,
                execution={
                    "rail": rail.name,
                    "tx_hash": result.tx_hash,
                    "actual_cost_usd": result.actual_cost_usd,
                    "merchant_receipt_id": result.merchant_receipt_id,
                },
            )

        # Anything else is a failure
        self.ledger.transition(
            intent.intent_id,
            IntentState.FAILED,
            reason=result.error or "unknown failure",
        )
        self.ledger.complete_execution(intent.intent_id, error=result.error)
        return BrokerResponse(
            intent_id=intent.intent_id,
            state=IntentState.FAILED.value,
            tier=tier,
            message=result.error or "unknown failure",
        )

    # ---- read paths -------------------------------------------------------

    def status(self, intent_id: str) -> dict[str, Any] | None:
        row = self.ledger.get_intent(intent_id)
        if row is None:
            return None
        return {
            "intent": {
                "intent_id": row["intent_id"],
                "merchant": row["merchant"],
                "amount_usd": row["amount_usd"],
                "tier": row["tier"],
            },
            "state": row["current_state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "audits": self.ledger.audits_for(intent_id),
            "execution": self.ledger.execution_for(intent_id),
            "transitions": self.ledger.state_history(intent_id),
        }

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.ledger.list_intents(limit)

    def check_balance(self) -> dict[str, Any]:
        """Return metamask balance + vast credit + spending so far.

        The actual balance check is delegated to the Chrome bridge (metamask
        extension API) and an optional merchant-API call. When those are not
        available we fall back to ledger-based spend info only.
        """
        spend_24h = self.ledger.spent_last_24h()
        spend_30d = self.ledger.spent_last_30d()
        balance: dict[str, Any] = {
            "spent_last_24h_usd": round(spend_24h, 2),
            "spent_last_30d_usd": round(spend_30d, 2),
            "cap_daily_usd": self.cfg.thresholds.daily_cap_usd,
            "cap_monthly_usd": self.cfg.thresholds.monthly_cap_usd,
            "remaining_today_usd": round(
                max(0.0, self.cfg.thresholds.daily_cap_usd - spend_24h), 2
            ),
            "remaining_month_usd": round(
                max(0.0, self.cfg.thresholds.monthly_cap_usd - spend_30d), 2
            ),
        }
        try:
            wallet_balance = self.chrome.query_metamask_balance_usdc()
            if wallet_balance is not None:
                balance["metamask_usdc"] = wallet_balance
        except ChromeUnavailableError:
            balance["metamask_usdc"] = None
            balance["metamask_note"] = "Chrome not running; balance unknown"
        return balance

    # ---- helpers ----------------------------------------------------------

    def _check_caps(self, intent: Intent) -> str | None:
        spent_24h = self.ledger.spent_last_24h()
        if spent_24h + intent.amount_usd > self.cfg.thresholds.daily_cap_usd:
            return (
                f"daily cap ${self.cfg.thresholds.daily_cap_usd:.2f} would be exceeded "
                f"(spent ${spent_24h:.2f}, this intent ${intent.amount_usd:.2f})"
            )
        spent_30d = self.ledger.spent_last_30d()
        if spent_30d + intent.amount_usd > self.cfg.thresholds.monthly_cap_usd:
            return (
                f"monthly cap ${self.cfg.thresholds.monthly_cap_usd:.2f} would be exceeded "
                f"(spent ${spent_30d:.2f}, this intent ${intent.amount_usd:.2f})"
            )
        return None

    @staticmethod
    def _row_to_intent(row: dict[str, Any]) -> Intent:
        import json

        return Intent.from_dict(
            {
                "intent_id": row["intent_id"],
                "merchant": row["merchant"],
                "amount_usd": row["amount_usd"],
                "rationale": row["rationale"],
                "estimated_actual_cost_usd": row["estimated_actual_cost_usd"],
                "references": json.loads(row["references_json"] or "[]"),
                "rail_hint": row.get("rail_hint"),
                "issuer_session": row["issuer_session"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
        )
