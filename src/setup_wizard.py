"""Interactive first-run wizard.

Covers:
  1. Prerequisites (Python, Chrome)
  2. Auditor selection (Codex, Claude, or both in shadow mode)
  3. Payment method enrollment (card / crypto / email-link)
  4. Merchant allowlist review
  5. Policy thresholds
  6. Dry-run smoke test
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, Prompt

from .config import (
    Config,
    MerchantConfig,
    PaymentMethod,
    load_config,
    save_config,
)
from .email_lock import (
    EmailLockError,
    EmailLockViolation,
    load_locked_email,
    lock_email,
)
from .ledger import init_ledger
from .paths import default_policy_path, env_path, local_root

console = Console()


def step(idx: int, total: int, title: str) -> None:
    console.rule(f"[bold cyan]Step {idx}/{total}[/] — {title}")


def ok(msg: str) -> None:
    console.print(f"  [green]ok[/]  {msg}")


def warn(msg: str) -> None:
    console.print(f"  [yellow]!![/]  {msg}")


def fail(msg: str) -> None:
    console.print(f"  [red]xx[/]  {msg}")


# --------------------------------------------------------------------------


def check_prereqs() -> bool:
    all_ok = True

    if sys.version_info < (3, 11):
        fail(f"Python >= 3.11 required, found {sys.version}")
        all_ok = False
    else:
        ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    chrome = next((p for p in chrome_paths if Path(p).exists()), None)
    if not chrome and not shutil.which("google-chrome"):
        fail("Google Chrome not detected. Install from google.com/chrome")
        all_ok = False
    else:
        ok(f"Chrome at {chrome or shutil.which('google-chrome')}")

    return all_ok


# --------------------------------------------------------------------------


def configure_auditor(cfg: Config) -> None:
    console.print(
        Panel.fit(
            "[bold]Auditor[/] is the second opinion that reviews every payment intent "
            "before the broker spends money. Codex (OpenAI) is recommended because "
            "Claude-auditing-Claude shares training biases.\n\n"
            "A: Codex (OpenAI)    — recommended primary\n"
            "B: Claude (Anthropic) — fallback (same-family, weaker)\n"
            "C: Both (shadow mode) — Codex decides, Claude runs in parallel",
            title="Audit layer",
            border_style="cyan",
        )
    )
    choice = Prompt.ask("Your choice", choices=["A", "B", "C"], default="A")

    codex_ok = bool(shutil.which("codex"))
    if choice in ("A", "C"):
        if not codex_ok:
            warn("`codex` CLI not found on PATH.")
            if Confirm.ask("Install codex CLI now via pip?", default=False):
                subprocess.run([sys.executable, "-m", "pip", "install", "codex-cli"], check=False)
                codex_ok = bool(shutil.which("codex"))

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            entered = Prompt.ask(
                "Paste your OpenAI API key (Enter to skip and use `codex login`)",
                default="",
                password=True,
            )
            if entered:
                _append_env("OPENAI_API_KEY", entered)
                os.environ["OPENAI_API_KEY"] = entered
        if not codex_ok:
            warn("Codex will be unavailable until you install the CLI + configure auth.")

    if choice in ("B", "C"):
        anth_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anth_key:
            entered = Prompt.ask(
                "Paste your Anthropic API key", default="", password=True
            )
            if entered:
                _append_env("ANTHROPIC_API_KEY", entered)
                os.environ["ANTHROPIC_API_KEY"] = entered

    cfg.audit.primary = {"A": "codex", "B": "claude", "C": "codex"}[choice]
    cfg.audit.shadow_mode = choice == "C"

    if choice == "B":
        warn(
            "Claude-auditing-Claude has shared-bias risk. Consider adding Codex "
            "later — rerun `broker setup`."
        )
    ok(f"audit.primary = {cfg.audit.primary}, shadow_mode = {cfg.audit.shadow_mode}")


# --------------------------------------------------------------------------


def configure_email_lock() -> None:
    """Lock the confirmation email (write-once)."""
    try:
        existing = load_locked_email()
    except EmailLockError as e:
        fail(f"email lock file is tampered: {e}")
        return
    if existing:
        ok(f"email already locked: {existing}")
        console.print(
            "[dim]this lock is permanent for this install. "
            "Reset requires `broker email-lock --reset` — see docs.[/]"
        )
        return

    console.print(
        Panel.fit(
            "[bold]Confirmation email (write-once)[/]\n\n"
            "Every payment intent is preceded by a 6-digit code sent to this "
            "address. Once set, the email cannot be changed without running "
            "`broker email-lock --reset` explicitly — an agent that got "
            "compromised mid-conversation cannot silently reroute your OTP "
            "channel to an attacker's inbox.\n\n"
            "This is independent of whatever account email you use with each "
            "merchant. It is the [italic]broker's own[/] authorization channel.",
            title="Email lock",
            border_style="cyan",
        )
    )
    while True:
        address = Prompt.ask("Confirmation email address").strip()
        if not address:
            warn("can't be blank")
            continue
        try:
            lock_email(address)
        except EmailLockViolation as e:
            fail(str(e))
            return
        except EmailLockError as e:
            warn(str(e))
            continue
        ok(f"locked: {address}")
        break


def configure_smtp() -> None:
    """SMTP credentials for sending OTP emails. Stored in .env, NOT config.yaml."""
    console.print(
        "[bold]SMTP for OTP delivery[/] — we send the 6-digit code via SMTP. "
        "For Gmail, use an [italic]app password[/] (Account → Security → App "
        "passwords); do NOT use your main password."
    )
    has_smtp = bool(os.environ.get("KYA_BROKER_SMTP_HOST"))
    if has_smtp:
        ok(f"SMTP already configured via env ({os.environ['KYA_BROKER_SMTP_HOST']})")
        if not Confirm.ask("Reconfigure?", default=False):
            return

    host = Prompt.ask("SMTP host", default="smtp.gmail.com")
    port = Prompt.ask("SMTP port (465 for SSL, 587 for STARTTLS)", default="465")
    user = Prompt.ask("SMTP user (the sender address)")
    pw = Prompt.ask("SMTP password / app-password", password=True)
    from_addr = Prompt.ask("From address (defaults to user)", default=user)
    use_ssl = port == "465"

    for key, val in [
        ("KYA_BROKER_SMTP_HOST", host),
        ("KYA_BROKER_SMTP_PORT", port),
        ("KYA_BROKER_SMTP_USER", user),
        ("KYA_BROKER_SMTP_PASS", pw),
        ("KYA_BROKER_SMTP_FROM", from_addr),
        ("KYA_BROKER_SMTP_USE_SSL", "true" if use_ssl else "false"),
    ]:
        _append_env(key, val)
        os.environ[key] = val
    ok("SMTP credentials written to .env")

    if Confirm.ask(
        "Send a test OTP email now to verify SMTP works?", default=True
    ):
        _smtp_test_send()


def _smtp_test_send() -> None:
    from email.message import EmailMessage

    from .email_verifier import SmtpConfig, _send_email

    smtp = SmtpConfig.from_env()
    if smtp is None:
        fail("SMTP env vars not picked up")
        return
    try:
        locked = load_locked_email()
    except EmailLockError:
        locked = None
    target = locked or smtp.user
    msg = EmailMessage()
    msg["Subject"] = "[KYA-Broker] SMTP test"
    msg.set_content(
        "This is a test message from your KYA-Broker setup. If you received it, "
        "SMTP is configured correctly."
    )
    try:
        _send_email(msg, target, smtp)
    except Exception as e:  # noqa: BLE001
        fail(f"test send failed: {e}")
        return
    ok(f"test email sent to {target}")


def configure_payment_methods(cfg: Config) -> None:
    console.print(
        Panel.fit(
            "[bold]Payment methods[/] are the rails the broker may use on your "
            "behalf. The broker never stores card numbers or seed phrases — we "
            "store only the label, rail, and last-4 / wallet address so the UI "
            "can show which method was charged.\n\n"
            "Pick any combination of:\n"
            "  • [bold]card[/]        — credit/debit via Chrome autofill, 1Password, "
            "Apple Pay, or manual entry at checkout time\n"
            "  • [bold]crypto[/]      — MetaMask / WalletConnect with USDC on a cheap chain\n"
            "  • [bold]email_link[/] — merchants that use email OTP / magic links\n\n"
            "You can enroll multiple methods per rail.",
            title="Payment methods",
            border_style="cyan",
        )
    )

    existing_names = {pm.name for pm in cfg.payment_methods}
    methods: list[PaymentMethod] = list(cfg.payment_methods)

    while True:
        if methods:
            console.print("[dim]current methods:[/] " + ", ".join(f"{m.name} ({m.rail})" for m in methods))
        if not Confirm.ask("Add a payment method?", default=len(methods) == 0):
            break
        rail = Prompt.ask(
            "Rail", choices=["card", "crypto", "email_link"], default="card"
        )
        default_name = {
            "card": "personal card",
            "crypto": "metamask main",
            "email_link": "email magic link",
        }[rail]
        while True:
            name = Prompt.ask("Label (short nickname)", default=default_name).strip()
            if name and name not in existing_names:
                break
            warn(f"{name!r} is already used; pick a different label.")
        existing_names.add(name)

        pm = PaymentMethod(name=name, rail=rail)
        if rail == "card":
            last4 = Prompt.ask("Last 4 digits (optional, for display only)", default="").strip()
            pm.last4 = last4 or None
            pm.notes = Prompt.ask("Notes (e.g. 'chrome autofill work profile')", default="").strip()
        elif rail == "crypto":
            addr = Prompt.ask("Wallet address (first + last chars shown in logs)", default="").strip()
            pm.wallet_address = addr or None
            pm.notes = Prompt.ask("Notes (e.g. 'MetaMask · Polygon · USDC')", default="").strip()
        else:
            pm.notes = Prompt.ask(
                "Email address for magic-link delivery (notes only)", default=""
            ).strip()

        if Confirm.ask("Set a per-method auto-execute ceiling (USD)?", default=False):
            pm.max_auto_execute_usd = FloatPrompt.ask(
                "Max auto-execute for this method (USD)", default=25.0
            )
        methods.append(pm)
        ok(f"added {name} ({rail})")

    cfg.payment_methods = methods

    # Keep rails preference aligned with enrolled rails. Push enrolled rails
    # to the top of cfg.rails in the order the user enrolled them.
    enrolled_order: list[str] = []
    for m in methods:
        if m.rail not in enrolled_order:
            enrolled_order.append(m.rail)
    for r in cfg.rails:
        if r not in enrolled_order:
            enrolled_order.append(r)
    cfg.rails = enrolled_order


# --------------------------------------------------------------------------


def review_merchants(cfg: Config) -> None:
    if not cfg.merchants:
        # fall back to default policy list
        raw = yaml.safe_load(default_policy_path().read_text(encoding="utf-8"))
        cfg.merchants = [_merchant_from_dict(m) for m in raw.get("merchants", [])]

    console.print(
        "[bold]Merchant allowlist[/] — the broker refuses any intent targeting a "
        "merchant not listed here. Default list covers openrouter.ai, vast.ai, "
        "anthropic.com. Review and remove / keep each."
    )
    kept: list[MerchantConfig] = []
    for m in cfg.merchants:
        rails = ", ".join(m.playbooks.keys()) or "(none)"
        keep = Confirm.ask(
            f"Keep {m.name} (rails: {rails}, cap ${m.max_single_topup_usd:.0f})?",
            default=True,
        )
        if keep:
            kept.append(m)
    cfg.merchants = kept


# --------------------------------------------------------------------------


def configure_policy(cfg: Config) -> None:
    console.print(
        "[bold]Spending thresholds[/] decide when human gates kick in:\n"
        "  L0 (<= l0_ceiling): audit approves → auto-execute (rail's own gate still runs)\n"
        "  L1: audit approves, rail gate must be completed by human\n"
        "  L2 (> l1_ceiling): broker halts, asks user out of band\n"
    )
    cfg.thresholds.l0_ceiling_usd = FloatPrompt.ask(
        "L0 ceiling (USD)", default=cfg.thresholds.l0_ceiling_usd
    )
    cfg.thresholds.l1_ceiling_usd = FloatPrompt.ask(
        "L1 ceiling (USD)", default=cfg.thresholds.l1_ceiling_usd
    )
    cfg.thresholds.daily_cap_usd = FloatPrompt.ask(
        "Daily cap (USD)", default=cfg.thresholds.daily_cap_usd
    )
    cfg.thresholds.monthly_cap_usd = FloatPrompt.ask(
        "Monthly cap (USD)", default=cfg.thresholds.monthly_cap_usd
    )


# --------------------------------------------------------------------------


def smoke_test(cfg: Config) -> bool:
    from .auditor import select_auditor

    try:
        primary, shadow = select_auditor(cfg)
        ok(f"audit primary = {primary.name}, shadow = {[s.name for s in shadow] or '[]'}")
    except Exception as e:  # noqa: BLE001
        fail(f"audit setup: {e}")
        return False

    init_ledger()
    ok(f"ledger initialised at {local_root() / 'ledger.sqlite'}")

    # Dry-run end-to-end: picks a small amount and a valid merchant + enrolled rail.
    os.environ["KYA_BROKER_DRY_RUN"] = "1"
    os.environ["KYA_BROKER_DRY_RUN_OUTCOME"] = "settled"
    os.environ.setdefault("KYA_BROKER_DRY_RUN_AUDITOR", "approve")
    os.environ["KYA_BROKER_DRY_RUN_HUMAN_GATE"] = "completed"
    # In smoke test, bypass the email OTP's popup round-trip by providing a
    # stub verifier that auto-approves. Real runs use the real verifier.
    os.environ["KYA_BROKER_SMOKE_SKIP_OTP"] = "1"

    import asyncio

    from .auditor.base import AuditContext
    from .broker import Broker

    merchant = cfg.merchants[0].name if cfg.merchants else "openrouter.ai"
    rail_hint = cfg.payment_methods[0].rail if cfg.payment_methods else None

    # Auto-lock demo email for smoke test if none is locked
    if load_locked_email() is None:
        lock_email("setup-smoke@example.com")

    async def _run() -> bool:
        broker = Broker(config=cfg)
        resp = await broker.propose_intent(
            {
                "merchant": merchant,
                "amount_usd": 0.5,
                "rationale": "setup wizard dry-run — no real money moves in DRY_RUN mode",
                "estimated_actual_cost_usd": 0.5,
                "rail_hint": rail_hint,
            },
            AuditContext(
                conversation_excerpt=(
                    "User is running the KYA-Broker setup wizard and asked for a dry-run "
                    "smoke test to confirm the full pipeline (audit + rail + human gate "
                    "+ ledger) works end-to-end."
                ),
            ),
        )
        return resp.state in {"settled", "rejected"}

    try:
        if asyncio.run(_run()):
            ok("broker dry-run completed")
            return True
        fail("broker dry-run returned unexpected state")
        return False
    except Exception as e:  # noqa: BLE001
        fail(f"dry-run failed: {e}")
        return False


def write_config(cfg: Config) -> None:
    save_config(cfg)
    ok(f"config written to {local_root() / 'config.yaml'}")


def _merchant_from_dict(d: dict) -> MerchantConfig:
    playbooks = d.get("playbooks") or (
        {d.get("preferred_rail", "card"): d["playbook"]} if d.get("playbook") else {}
    )
    return MerchantConfig(
        name=d["name"],
        playbooks={str(k): str(v) for k, v in playbooks.items()},
        max_single_topup_usd=float(d.get("max_single_topup_usd", 50.0)),
        preferred_rail=str(d.get("preferred_rail", "card")),
        homepage_url=str(d.get("homepage_url", "")),
        credit_page_url=str(d.get("credit_page_url", "")),
        notes=str(d.get("notes", "")),
    )


def _append_env(key: str, value: str) -> None:
    p = env_path()
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    lines = [ln for ln in existing.splitlines() if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------


def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]KYA-Broker setup[/]\n"
            "Lets Claude Code autonomously pay merchants (OpenRouter, vast.ai, "
            "Anthropic, …) using your human payment methods. The skill drives the "
            "browser; you authorise each payment at the card / wallet / email step.",
            border_style="cyan",
        )
    )

    step(1, 8, "Prerequisites")
    if not check_prereqs():
        if not Confirm.ask("Prereqs not fully satisfied. Continue anyway?", default=False):
            sys.exit(1)

    # Load or seed config
    try:
        cfg = load_config()
    except Exception:
        raw = default_policy_path().read_text(encoding="utf-8")
        (local_root() / "config.yaml").write_text(raw, encoding="utf-8")
        cfg = load_config()

    step(2, 8, "Confirmation email (write-once)")
    configure_email_lock()

    step(3, 8, "SMTP for OTP delivery")
    configure_smtp()

    step(4, 8, "Audit layer")
    configure_auditor(cfg)

    step(5, 8, "Enroll payment methods")
    configure_payment_methods(cfg)

    step(6, 8, "Merchant allowlist")
    review_merchants(cfg)

    step(7, 8, "Spending thresholds")
    configure_policy(cfg)

    write_config(cfg)

    step(8, 8, "Smoke test")
    if smoke_test(cfg):
        console.print(Panel.fit("[bold green]Setup complete.[/]", border_style="green"))
    else:
        console.print(
            Panel.fit(
                "[yellow]Setup mostly complete, but smoke test did not pass.[/]\n"
                "Rerun `broker setup` after fixing the warnings above.",
                border_style="yellow",
            )
        )


if __name__ == "__main__":
    main()
