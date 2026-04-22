"""User config loader with environment-variable overrides for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import config_path, default_policy_path, env_path


class ConfigError(Exception):
    pass


@dataclass
class AuditCodexConfig:
    model: str = "gpt-5-codex"
    binary_path: str | None = None


@dataclass
class AuditClaudeConfig:
    model: str = "claude-sonnet-4-6"
    max_output_tokens: int = 2000


@dataclass
class AuditConfig:
    primary: str = "auto"  # codex | claude | auto
    shadow_mode: bool = False
    fallback_on_primary_failure: bool = False
    timeout_seconds: int = 30
    codex: AuditCodexConfig = field(default_factory=AuditCodexConfig)
    claude: AuditClaudeConfig = field(default_factory=AuditClaudeConfig)


@dataclass
class Thresholds:
    l0_ceiling_usd: float = 2.00
    l1_ceiling_usd: float = 50.00
    daily_cap_usd: float = 200.00
    monthly_cap_usd: float = 1000.00


@dataclass
class MerchantConfig:
    name: str
    playbook: str
    max_single_topup_usd: float = 50.00
    preferred_rail: str = "crypto"


@dataclass
class ChromeConfig:
    binary_path: str | None = None
    cdp_port: int = 9222
    profile_dir: str | None = None
    metamask_popup_timeout_s: int = 300


@dataclass
class ObservabilityConfig:
    capture_on_failure: bool = True
    retain_raw_audit_output: bool = True


@dataclass
class Config:
    version: int = 1
    thresholds: Thresholds = field(default_factory=Thresholds)
    audit: AuditConfig = field(default_factory=AuditConfig)
    merchants: list[MerchantConfig] = field(default_factory=list)
    rails: list[str] = field(default_factory=lambda: ["crypto"])
    chrome: ChromeConfig = field(default_factory=ChromeConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)

    def merchant(self, name: str) -> MerchantConfig | None:
        for m in self.merchants:
            if m.name == name:
                return m
        return None


def _load_env_file(path: Path) -> None:
    """Minimal .env loader that populates os.environ without overwriting existing keys."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _dict_to_config(raw: dict[str, Any]) -> Config:
    thresholds = Thresholds(**raw.get("thresholds", {}))

    audit_raw = raw.get("audit", {}) or {}
    codex = AuditCodexConfig(**audit_raw.get("codex", {}))
    claude = AuditClaudeConfig(**audit_raw.get("claude", {}))
    audit = AuditConfig(
        primary=audit_raw.get("primary", "auto"),
        shadow_mode=bool(audit_raw.get("shadow_mode", False)),
        fallback_on_primary_failure=bool(audit_raw.get("fallback_on_primary_failure", False)),
        timeout_seconds=int(audit_raw.get("timeout_seconds", 30)),
        codex=codex,
        claude=claude,
    )

    merchants = [MerchantConfig(**m) for m in raw.get("merchants", [])]
    chrome = ChromeConfig(**raw.get("chrome", {}))
    obs = ObservabilityConfig(**raw.get("observability", {}))

    return Config(
        version=int(raw.get("version", 1)),
        thresholds=thresholds,
        audit=audit,
        merchants=merchants,
        rails=list(raw.get("rails", ["crypto"])),
        chrome=chrome,
        observability=obs,
    )


def load_config() -> Config:
    """Load config.yaml, falling back to policy.default.yaml if not yet configured.

    Env vars (.env file in local_root) are loaded into os.environ as a side effect.
    Secrets (OPENAI_API_KEY, ANTHROPIC_API_KEY, VAST_API_KEY, etc.) are NEVER
    stored in config.yaml — always in .env.
    """
    _load_env_file(env_path())

    target = config_path()
    if not target.exists():
        target = default_policy_path()
        if not target.exists():
            raise ConfigError(
                f"Neither {config_path()} nor {default_policy_path()} exists. "
                "Run `kya-broker-setup` first."
            )

    with target.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return _dict_to_config(raw)


def save_config(cfg: Config) -> None:
    """Dump config back to disk (used by setup wizard after interactive edits)."""
    data: dict[str, Any] = {
        "version": cfg.version,
        "thresholds": cfg.thresholds.__dict__,
        "audit": {
            "primary": cfg.audit.primary,
            "shadow_mode": cfg.audit.shadow_mode,
            "fallback_on_primary_failure": cfg.audit.fallback_on_primary_failure,
            "timeout_seconds": cfg.audit.timeout_seconds,
            "codex": cfg.audit.codex.__dict__,
            "claude": cfg.audit.claude.__dict__,
        },
        "merchants": [m.__dict__ for m in cfg.merchants],
        "rails": cfg.rails,
        "chrome": cfg.chrome.__dict__,
        "observability": cfg.observability.__dict__,
    }
    with config_path().open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
