"""Write-once email lock for the user's confirmation email.

Design goals:
  * The user declares their confirmation email at the start of the first
    conversation that uses the skill. That email is the channel through which
    the broker sends OTPs during payment flow.
  * Once locked, it cannot be changed within the "project cycle". A compromised
    agent (prompt injection, etc.) cannot swap the email to an attacker's
    address and silently reroute OTPs.
  * Reset requires an explicit user action outside the agent's control:
    physically deleting `email_lock.json` from the local state dir, or running
    `broker email-lock --reset --i-understand-this-invalidates-past-intents`.

Storage (`~/.claude/skills/kya-broker.local/email_lock.json`):

    {
      "email": "user@example.com",
      "sha256_with_salt": "hex…",
      "locked_at": "2026-04-22T03:11:00Z",
      "version": 1
    }

The file is chmodded 0o444 (read-only) after write. The sha256 field is a
tamper indicator: even if someone edits the `email` field, the hash won't
match, and `load_locked_email()` refuses to return a mismatched record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import local_root


# A per-install salt so the hash can't be compared across machines.
# Stored alongside the lock file the first time the lock is written.
_SALT_FILE = "email_lock.salt"


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class EmailLockError(Exception):
    pass


class EmailLockViolation(EmailLockError):
    """Raised when someone tries to lock a different email while one is already locked."""


class EmailLockTampered(EmailLockError):
    """Raised when the hash doesn't match the stored email (file was edited)."""


@dataclass
class EmailLock:
    email: str
    sha256_with_salt: str
    locked_at: str
    version: int = 1


def _lock_path() -> Path:
    return local_root() / "email_lock.json"


def _salt_path() -> Path:
    return local_root() / _SALT_FILE


def _get_or_create_salt() -> str:
    p = _salt_path()
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    # Use secrets for high-entropy salt
    import secrets

    salt = secrets.token_hex(16)
    p.write_text(salt, encoding="utf-8")
    os.chmod(p, 0o400)
    return salt


def _hash_email(email: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{email.strip().lower()}".encode("utf-8")).hexdigest()


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def load_locked_email() -> str | None:
    """Return the currently locked email, or None if no lock exists.

    Raises EmailLockTampered if the file's hash doesn't match the stored email.
    """
    p = _lock_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise EmailLockTampered(f"email_lock.json is not valid JSON: {e}") from e

    email = data.get("email", "")
    stored_hash = data.get("sha256_with_salt", "")
    salt = _get_or_create_salt()
    expected = _hash_email(email, salt)
    if expected != stored_hash:
        raise EmailLockTampered(
            "email_lock.json hash mismatch — the file was edited outside the broker. "
            "Delete email_lock.json and email_lock.salt to reset (acknowledging that "
            "any in-flight intents should be considered invalid)."
        )
    return email


def lock_email(email: str) -> EmailLock:
    """Set the one-time confirmation email. Refuses if a different email is already locked.

    Idempotent: calling with the same email again is a no-op.
    """
    email = email.strip().lower()
    if not _valid_email(email):
        raise EmailLockError(f"{email!r} is not a syntactically valid email address")

    existing = load_locked_email()
    if existing is not None and existing != email:
        raise EmailLockViolation(
            f"email is already locked to {existing!r}; cannot change to {email!r}. "
            "This lock is intentional — a compromised agent must not be able to reroute "
            "the OTP channel. To reset, run `broker email-lock --reset` (which requires "
            "explicit user confirmation and invalidates past intents)."
        )

    salt = _get_or_create_salt()
    lock = EmailLock(
        email=email,
        sha256_with_salt=_hash_email(email, salt),
        locked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        version=1,
    )
    p = _lock_path()
    # Ensure we can write even if the previous file was 0o444
    if p.exists():
        os.chmod(p, 0o600)
    p.write_text(json.dumps(lock.__dict__, indent=2), encoding="utf-8")
    os.chmod(p, 0o444)  # read-only
    return lock


def reset_lock(confirmation_token: str) -> None:
    """Blow away the lock. Requires caller to pass the explicit confirmation token.

    The token is a literal sentinel string, not a secret — the point is forcing
    the caller to opt in to a semantically dangerous operation. Agent code that
    accidentally calls this function will fail because they won't pass the token.
    """
    if confirmation_token != "I_UNDERSTAND_THIS_INVALIDATES_PAST_INTENTS":
        raise EmailLockError(
            "reset_lock requires confirmation_token="
            "'I_UNDERSTAND_THIS_INVALIDATES_PAST_INTENTS'"
        )
    for p in (_lock_path(), _salt_path()):
        if p.exists():
            try:
                os.chmod(p, 0o600)
            except PermissionError:
                pass
            p.unlink()


def ensure_locked(prompt_user: bool = False) -> str:
    """Return the locked email, or raise EmailLockError if none is locked.

    If prompt_user=True and run interactively, asks the user in the terminal.
    Callers in agent context should pass prompt_user=False and handle the
    raised error by telling the user to run `broker email-lock <address>`.
    """
    email = load_locked_email()
    if email:
        return email
    if not prompt_user:
        raise EmailLockError(
            "no confirmation email is locked. The user must run "
            "`broker email-lock <address>` once before any intent can settle."
        )
    entered = input("Enter the confirmation email address for this skill install: ").strip()
    return lock_email(entered).email
