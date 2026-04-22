"""Resolve skill install and user-local paths.

The skill lives in `~/.claude/skills/kya-broker/` (read-only, git-pullable)
and user state lives in `~/.claude/skills/kya-broker.local/` (never committed).
These paths can be overridden with env vars for development.
"""

from __future__ import annotations

import os
from pathlib import Path


def skill_root() -> Path:
    env = os.environ.get("KYA_BROKER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".claude" / "skills" / "kya-broker").resolve()


def local_root() -> Path:
    env = os.environ.get("KYA_BROKER_LOCAL")
    if env:
        p = Path(env).expanduser().resolve()
    else:
        p = (Path.home() / ".claude" / "skills" / "kya-broker.local").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def ledger_path() -> Path:
    return local_root() / "ledger.sqlite"


def config_path() -> Path:
    return local_root() / "config.yaml"


def env_path() -> Path:
    return local_root() / ".env"


def default_policy_path() -> Path:
    return skill_root() / "policy.default.yaml"


def playbook_dir() -> Path:
    return skill_root() / "playbooks"


def prompt_dir() -> Path:
    return skill_root() / "prompts"


def dumps_dir() -> Path:
    d = local_root() / "dumps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = local_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
