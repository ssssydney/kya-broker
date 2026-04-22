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
    """A merchant the broker is allowed to pay.

    playbooks is {rail_name: playbook_file.yaml}. The merchant must have at
    least one entry that matches an enrolled payment method.
    """

    name: str
    playbooks: dict[str, str] = field(default_factory=dict)
    max_single_topup_usd: float = 50.00
    preferred_rail: str = "card"
    homepage_url: str = ""
    credit_page_url: str = ""
    notes: str = ""


@dataclass
class PaymentMethod:
    """A human-enrollable way to pay. Multiple methods per rail are fine
    (e.g. a personal card and a research-budget card), but the broker picks
    the first matching method at execution time unless `intent.rail_hint` is
    more specific.
    """

    name: str                                     # user-chosen label, e.g. "research budget visa"
    rail: str                                     # card | crypto | email_link | bank_transfer
    last4: str | None = None                      # last 4 for cards (NOT full number)
    wallet_address: str | None = None             # for crypto, for display only
    notes: str = ""
    max_auto_execute_usd: float | None = None     # optional per-method ceiling


@dataclass
class ChromeConfig:
    binary_path: str | None = None
    cdp_port: int = 9222
    profile_dir: str | None = None
    human_gate_timeout_s: int = 300
    # Legacy alias kept for backward compat with v0.3.1 configs
    metamask_popup_timeout_s: int | None = None

    def __post_init__(self) -> None:
        if self.metamask_popup_timeout_s is not None:
            # honor the older field if the user's config still has it
            self.human_gate_timeout_s = int(self.metamask_popup_timeout_s)


@dataclass
class NotificationConfig:
    channels: list[str] = field(default_factory=lambda: ["terminal"])
    poll_interval_s: float = 1.0


@dataclass
class ObservabilityConfig:
    capture_on_failure: bool = True
    retain_raw_audit_output: bool = True


@dataclass
class Config:
    version: int = 2
    thresholds: Thresholds = field(default_factory=Thresholds)
    audit: AuditConfig = field(default_factory=AuditConfig)
    merchants: list[MerchantConfig] = field(default_factory=list)
    payment_methods: list[PaymentMethod] = field(default_factory=list)
    rails: list[str] = field(default_factory=lambda: ["card", "crypto", "email_link"])
    chrome: ChromeConfig = field(default_factory=ChromeConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)

    def merchant(self, name: str) -> MerchantConfig | None:
        for m in self.merchants:
            if m.name == name:
                return m
        return None

    def payment_method(self, name: str) -> PaymentMethod | None:
        for pm in self.payment_methods:
            if pm.name == name:
                return pm
        return None


# --------------------------------------------------------------------------


def _load_env_file(path: Path) -> None:
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


def _merchant_from_dict(d: dict[str, Any]) -> MerchantConfig:
    # Accept both the old `playbook: file.yaml` and the new
    # `playbooks: {rail: file.yaml}` shapes.
    playbooks = d.get("playbooks")
    if playbooks is None and "playbook" in d:
        playbooks = {d.get("preferred_rail", "card"): d["playbook"]}
    playbooks = playbooks or {}
    return MerchantConfig(
        name=d["name"],
        playbooks={str(k): str(v) for k, v in playbooks.items()},
        max_single_topup_usd=float(d.get("max_single_topup_usd", 50.0)),
        preferred_rail=str(d.get("preferred_rail", "card")),
        homepage_url=str(d.get("homepage_url", "")),
        credit_page_url=str(d.get("credit_page_url", "")),
        notes=str(d.get("notes", "")),
    )


def _payment_method_from_dict(d: dict[str, Any]) -> PaymentMethod:
    return PaymentMethod(
        name=str(d["name"]),
        rail=str(d["rail"]),
        last4=d.get("last4"),
        wallet_address=d.get("wallet_address"),
        notes=str(d.get("notes", "")),
        max_auto_execute_usd=(
            float(d["max_auto_execute_usd"]) if d.get("max_auto_execute_usd") is not None else None
        ),
    )


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

    merchants = [_merchant_from_dict(m) for m in raw.get("merchants", [])]
    payment_methods = [_payment_method_from_dict(m) for m in raw.get("payment_methods", [])]

    chrome_raw = raw.get("chrome", {}) or {}
    chrome = ChromeConfig(**chrome_raw)

    notifications_raw = raw.get("notifications", {}) or {}
    notifications = NotificationConfig(
        channels=list(notifications_raw.get("channels", ["terminal"])),
        poll_interval_s=float(notifications_raw.get("poll_interval_s", 1.0)),
    )

    obs = ObservabilityConfig(**raw.get("observability", {}))

    rails = list(raw.get("rails", ["card", "crypto", "email_link"]))

    return Config(
        version=int(raw.get("version", 2)),
        thresholds=thresholds,
        audit=audit,
        merchants=merchants,
        payment_methods=payment_methods,
        rails=rails,
        chrome=chrome,
        notifications=notifications,
        observability=obs,
    )


def load_config() -> Config:
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
        "merchants": [
            {
                "name": m.name,
                "playbooks": m.playbooks,
                "max_single_topup_usd": m.max_single_topup_usd,
                "preferred_rail": m.preferred_rail,
                "homepage_url": m.homepage_url,
                "credit_page_url": m.credit_page_url,
                "notes": m.notes,
            }
            for m in cfg.merchants
        ],
        "payment_methods": [
            {
                "name": pm.name,
                "rail": pm.rail,
                "last4": pm.last4,
                "wallet_address": pm.wallet_address,
                "notes": pm.notes,
                "max_auto_execute_usd": pm.max_auto_execute_usd,
            }
            for pm in cfg.payment_methods
        ],
        "rails": cfg.rails,
        "chrome": {
            "binary_path": cfg.chrome.binary_path,
            "cdp_port": cfg.chrome.cdp_port,
            "profile_dir": cfg.chrome.profile_dir,
            "human_gate_timeout_s": cfg.chrome.human_gate_timeout_s,
        },
        "notifications": {
            "channels": cfg.notifications.channels,
            "poll_interval_s": cfg.notifications.poll_interval_s,
        },
        "observability": cfg.observability.__dict__,
    }
    with config_path().open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
