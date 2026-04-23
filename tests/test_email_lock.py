"""Tests for the write-once email lock."""

from __future__ import annotations

import os

import pytest

from src.email_lock import (
    EmailLockError,
    EmailLockTampered,
    EmailLockViolation,
    load_locked_email,
    lock_email,
    reset_lock,
)


def test_lock_persists():
    lock_email("warren@example.com")
    assert load_locked_email() == "warren@example.com"


def test_lock_idempotent_with_same_email():
    lock_email("warren@example.com")
    lock_email("WARREN@example.com")  # different case, same address
    assert load_locked_email() == "warren@example.com"


def test_lock_refuses_different_email():
    lock_email("warren@example.com")
    with pytest.raises(EmailLockViolation):
        lock_email("attacker@evil.com")
    # Original still stands
    assert load_locked_email() == "warren@example.com"


def test_lock_rejects_invalid_syntax():
    with pytest.raises(EmailLockError):
        lock_email("not-an-email")


def test_reset_requires_confirmation_token():
    lock_email("warren@example.com")
    with pytest.raises(EmailLockError):
        reset_lock("wrong token")
    assert load_locked_email() == "warren@example.com"


def test_reset_with_token_clears():
    lock_email("warren@example.com")
    reset_lock("I_UNDERSTAND_THIS_INVALIDATES_PAST_INTENTS")
    assert load_locked_email() is None


def test_reset_allows_new_lock():
    lock_email("warren@example.com")
    reset_lock("I_UNDERSTAND_THIS_INVALIDATES_PAST_INTENTS")
    lock_email("new-address@example.com")
    assert load_locked_email() == "new-address@example.com"


def test_tamper_detection(isolated_state):
    lock_email("warren@example.com")
    path = isolated_state / "email_lock.json"
    os.chmod(path, 0o600)
    data = path.read_text()
    # Tamper the email field directly, leaving the hash in place
    tampered = data.replace("warren@example.com", "attacker@evil.com")
    path.write_text(tampered)
    with pytest.raises(EmailLockTampered):
        load_locked_email()


def test_lockfile_readonly_after_write(isolated_state):
    lock_email("warren@example.com")
    path = isolated_state / "email_lock.json"
    mode = path.stat().st_mode & 0o777
    # Should be 0o444 (read-only for all)
    assert mode & 0o222 == 0, f"lock file should be read-only, got mode {oct(mode)}"


def test_load_when_no_lock_returns_none():
    assert load_locked_email() is None
