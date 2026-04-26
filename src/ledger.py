"""Thin SQLite ledger for v1.0.

What it stores:
  * intents (one row per agent attempt: merchant, amount, rationale, status, timestamps)
  * budget caps (daily / monthly)

What it does NOT do:
  * No browser driving — that's the agent's job via Claude-in-Chrome MCP
  * No auditor — the agent's chat conversation with the user IS the audit
  * No email / OTP / popup — the user's "yes" in chat IS the gate

Storage: `~/.claude/skills/kya-broker.local/ledger.sqlite`. Survives reinstalls;
the bootstrap script never touches it once created.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS intents (
    intent_id  TEXT PRIMARY KEY,
    merchant   TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    rationale  TEXT,
    status     TEXT NOT NULL,        -- proposed | settled | failed | declined | cancelled
    note       TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intents_created ON intents(created_at);
CREATE INDEX IF NOT EXISTS idx_intents_merchant ON intents(merchant);
CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);

CREATE TABLE IF NOT EXISTS budget (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL
);
"""


VALID_STATUSES = frozenset({"proposed", "settled", "failed", "declined", "cancelled"})


def _local_root() -> Path:
    env = os.environ.get("KYA_BROKER_LOCAL")
    if env:
        p = Path(env).expanduser().resolve()
    else:
        p = (Path.home() / ".claude" / "skills" / "kya-broker.local").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def ledger_path() -> Path:
    return _local_root() / "ledger.sqlite"


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
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()


class LedgerError(Exception):
    pass


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

    # ---- intents ----------------------------------------------------------

    def log_intent(
        self,
        merchant: str,
        amount_usd: float,
        rationale: str | None = None,
        status: str = "proposed",
        note: str | None = None,
    ) -> str:
        if amount_usd <= 0:
            raise LedgerError(f"amount_usd must be > 0, got {amount_usd}")
        if not merchant.strip():
            raise LedgerError("merchant is required")
        if status not in VALID_STATUSES:
            raise LedgerError(f"status must be one of {sorted(VALID_STATUSES)}")

        intent_id = str(uuid.uuid4())
        ts = _iso_now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (intent_id, merchant.strip(), float(amount_usd), rationale, status, note, ts, ts),
            )
        return intent_id

    def update_intent(
        self,
        intent_id: str,
        status: str | None = None,
        note: str | None = None,
    ) -> None:
        if status is not None and status not in VALID_STATUSES:
            raise LedgerError(f"status must be one of {sorted(VALID_STATUSES)}")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT intent_id FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"no such intent {intent_id}")
            updates: list[str] = []
            params: list[Any] = []
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if note is not None:
                updates.append("note = ?")
                params.append(note)
            if not updates:
                return
            updates.append("updated_at = ?")
            params.append(_iso_now())
            params.append(intent_id)
            conn.execute(
                f"UPDATE intents SET {', '.join(updates)} WHERE intent_id = ?", params
            )

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM intents ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- budgets ----------------------------------------------------------

    def get_budget(self) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM budget").fetchall()
            return {r["key"]: float(r["value"]) for r in rows}

    def set_budget(self, key: str, value: float) -> None:
        if key not in {"daily_cap_usd", "monthly_cap_usd"}:
            raise LedgerError(
                f"budget key must be 'daily_cap_usd' or 'monthly_cap_usd', got {key!r}"
            )
        if value < 0:
            raise LedgerError(f"value must be >= 0, got {value}")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO budget(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, float(value)),
            )

    def spent_within_hours(self, hours: float) -> float:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM intents "
                "WHERE status = 'settled' AND created_at >= ?",
                (cutoff,),
            ).fetchone()
            return float(row["total"] or 0.0)

    def check_budget(self, prospective_amount_usd: float) -> tuple[bool, str]:
        """Return (ok, reason). ok=True means amount fits within both caps."""
        budget = self.get_budget()
        d_cap = budget.get("daily_cap_usd")
        m_cap = budget.get("monthly_cap_usd")
        if d_cap is not None:
            spent = self.spent_within_hours(24)
            if spent + prospective_amount_usd > d_cap:
                return (
                    False,
                    f"would exceed daily cap ${d_cap:.2f} "
                    f"(spent ${spent:.2f}, this ${prospective_amount_usd:.2f})",
                )
        if m_cap is not None:
            spent = self.spent_within_hours(24 * 30)
            if spent + prospective_amount_usd > m_cap:
                return (
                    False,
                    f"would exceed monthly cap ${m_cap:.2f} "
                    f"(spent ${spent:.2f}, this ${prospective_amount_usd:.2f})",
                )
        return True, "ok"
