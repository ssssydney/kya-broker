"""SQLite ledger for intent state, audit results, executions, and spending.

Design goals:
- Durable across broker restarts and Claude Code sessions.
- Append-only history (no UPDATE without INSERT-to-events).
- Schema versioned so future migrations are safe.

The DB lives at `~/.claude/skills/kya-broker.local/ledger.sqlite` and is NOT part
of the skill repo — uninstalling the skill won't touch it, but the user can
wipe it manually.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .intent import Intent, IntentState, assert_transition
from .paths import ledger_path


SCHEMA_VERSION = 3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    merchant TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    rationale TEXT NOT NULL,
    estimated_actual_cost_usd REAL NOT NULL,
    references_json TEXT NOT NULL,
    rail_hint TEXT,
    issuer_session TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    current_state TEXT NOT NULL,
    tier TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT,
    metadata TEXT,  -- JSON blob
    created_at TEXT NOT NULL,
    FOREIGN KEY (intent_id) REFERENCES intents(intent_id)
);

CREATE TABLE IF NOT EXISTS audit_results (
    intent_id TEXT NOT NULL,
    auditor_name TEXT NOT NULL,
    is_primary INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    concerns TEXT,
    recommended_amount_usd REAL,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    raw_output TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (intent_id, auditor_name),
    FOREIGN KEY (intent_id) REFERENCES intents(intent_id)
);

CREATE TABLE IF NOT EXISTS executions (
    intent_id TEXT PRIMARY KEY,
    rail TEXT NOT NULL,
    tx_hash TEXT,
    merchant_receipt_id TEXT,
    actual_cost_usd REAL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    FOREIGN KEY (intent_id) REFERENCES intents(intent_id)
);

CREATE INDEX IF NOT EXISTS idx_intents_created_at ON intents(created_at);
CREATE INDEX IF NOT EXISTS idx_intents_merchant ON intents(merchant);
CREATE INDEX IF NOT EXISTS idx_state_events_intent ON state_events(intent_id);
CREATE INDEX IF NOT EXISTS idx_executions_started_at ON executions(started_at);
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_ledger(db_path: Path | None = None) -> None:
    path = db_path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_connect(path)) as conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            current = int(row["version"])
            if current < 3:
                # v3 added intents.rail_hint. Existing rows get NULL.
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(intents)").fetchall()
                }
                if "rail_hint" not in cols:
                    conn.execute("ALTER TABLE intents ADD COLUMN rail_hint TEXT")
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        conn.commit()


class Ledger:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or ledger_path()
        init_ledger(self.db_path)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = _connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- intents -----------------------------------------------------------

    def insert_intent(self, intent: Intent, tier: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO intents (
                    intent_id, merchant, amount_usd, rationale,
                    estimated_actual_cost_usd, references_json, rail_hint, issuer_session,
                    created_at, expires_at, current_state, tier, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    intent.merchant,
                    intent.amount_usd,
                    intent.rationale,
                    intent.estimated_actual_cost_usd,
                    json.dumps(intent.references),
                    intent.rail_hint,
                    intent.issuer_session,
                    intent.to_dict()["created_at"],
                    intent.to_dict()["expires_at"],
                    IntentState.PROPOSED.value,
                    tier,
                    _iso_now(),
                ),
            )
            conn.execute(
                """
                INSERT INTO state_events (intent_id, from_state, to_state, reason, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (intent.intent_id, None, IntentState.PROPOSED.value, "created", None, _iso_now()),
            )

    def transition(
        self,
        intent_id: str,
        new_state: IntentState,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT current_state FROM intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no such intent {intent_id}")
            old = IntentState(row["current_state"])
            assert_transition(old, new_state)

            ts = _iso_now()
            conn.execute(
                "UPDATE intents SET current_state = ?, updated_at = ? WHERE intent_id = ?",
                (new_state.value, ts, intent_id),
            )
            conn.execute(
                """
                INSERT INTO state_events (intent_id, from_state, to_state, reason, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    old.value,
                    new_state.value,
                    reason,
                    json.dumps(metadata) if metadata else None,
                    ts,
                ),
            )

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def list_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM intents ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def state_history(self, intent_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM state_events WHERE intent_id = ? ORDER BY id ASC",
                (intent_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- audits ------------------------------------------------------------

    def record_audit(
        self,
        intent_id: str,
        auditor_name: str,
        is_primary: bool,
        verdict: str,
        concerns: list[str],
        recommended_amount_usd: float | None,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        raw_output: str | None,
        model: str | None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_results (
                    intent_id, auditor_name, is_primary, verdict, concerns,
                    recommended_amount_usd, latency_ms, input_tokens, output_tokens,
                    raw_output, model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    auditor_name,
                    1 if is_primary else 0,
                    verdict,
                    json.dumps(concerns),
                    recommended_amount_usd,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    raw_output,
                    model,
                    _iso_now(),
                ),
            )

    def audits_for(self, intent_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_results WHERE intent_id = ?", (intent_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def audit_comparison(self, since_iso: str | None = None) -> list[dict[str, Any]]:
        """Return rows for A/B analysis: one row per intent with both auditor verdicts."""
        sql = """
            SELECT
                i.intent_id,
                i.merchant,
                i.amount_usd,
                i.created_at,
                MAX(CASE WHEN ar.auditor_name='codex' THEN ar.verdict END) AS codex_verdict,
                MAX(CASE WHEN ar.auditor_name='codex' THEN ar.concerns END) AS codex_concerns,
                MAX(CASE WHEN ar.auditor_name='codex' THEN ar.latency_ms END) AS codex_latency_ms,
                MAX(CASE WHEN ar.auditor_name='claude' THEN ar.verdict END) AS claude_verdict,
                MAX(CASE WHEN ar.auditor_name='claude' THEN ar.concerns END) AS claude_concerns,
                MAX(CASE WHEN ar.auditor_name='claude' THEN ar.latency_ms END) AS claude_latency_ms
            FROM intents i
            LEFT JOIN audit_results ar ON ar.intent_id = i.intent_id
            {where}
            GROUP BY i.intent_id
            ORDER BY i.created_at DESC
        """
        where = ""
        params: tuple[Any, ...] = ()
        if since_iso:
            where = "WHERE i.created_at >= ?"
            params = (since_iso,)
        with self._conn() as conn:
            rows = conn.execute(sql.format(where=where), params).fetchall()
            return [dict(r) for r in rows]

    # ---- executions --------------------------------------------------------

    def start_execution(self, intent_id: str, rail: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO executions (intent_id, rail, started_at)
                VALUES (?, ?, ?)
                """,
                (intent_id, rail, _iso_now()),
            )

    def complete_execution(
        self,
        intent_id: str,
        tx_hash: str | None = None,
        merchant_receipt_id: str | None = None,
        actual_cost_usd: float | None = None,
        error: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE executions
                SET tx_hash = COALESCE(?, tx_hash),
                    merchant_receipt_id = COALESCE(?, merchant_receipt_id),
                    actual_cost_usd = COALESCE(?, actual_cost_usd),
                    error = COALESCE(?, error),
                    completed_at = ?
                WHERE intent_id = ?
                """,
                (tx_hash, merchant_receipt_id, actual_cost_usd, error, _iso_now(), intent_id),
            )

    def execution_for(self, intent_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row) if row else None

    # ---- aggregates --------------------------------------------------------

    def spent_since(self, hours: float) -> float:
        """Sum settled actual_cost_usd (falling back to amount_usd) within last `hours`."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(e.actual_cost_usd, i.amount_usd)), 0) AS total
                FROM intents i
                LEFT JOIN executions e ON e.intent_id = i.intent_id
                WHERE i.current_state = 'settled' AND i.created_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            return float(row["total"] or 0.0)

    def spent_last_24h(self) -> float:
        return self.spent_since(24)

    def spent_last_30d(self) -> float:
        return self.spent_since(24 * 30)
