"""`broker` CLI — the human-facing interface over the same operations as MCP.

Commands:
  broker --version
  broker setup                           delegates to setup_wizard
  broker propose-intent <intent.json>    same semantics as MCP propose_intent
  broker status <intent_id>              print JSON state
  broker history [--limit N] [--format json|pretty]
  broker check-balance
  broker analyze-audits [--since YYYY-MM-DD]
  broker resume <intent_id>              mark an awaiting_user intent ready to execute
  broker export-logs <outfile.json>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .auditor import AuditContext
from .broker import Broker, BrokerError
from .email_lock import (
    EmailLockError,
    EmailLockViolation,
    load_locked_email,
    lock_email,
    reset_lock,
)
from .ledger import Ledger

console = Console()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="broker")
def cli() -> None:
    """KYA-Broker: autonomous payment skill for Claude Code."""


@cli.command("setup")
def setup_cmd() -> None:
    """Run the interactive setup wizard."""
    from .setup_wizard import main as wizard_main

    wizard_main()


@cli.command("propose-intent")
@click.argument("intent_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--context-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def propose_intent_cmd(intent_file: Path, context_file: Path | None) -> None:
    """Submit a payment intent described by JSON file."""
    payload = json.loads(intent_file.read_text(encoding="utf-8"))
    ctx = AuditContext()
    if context_file:
        cdata = json.loads(context_file.read_text(encoding="utf-8"))
        ctx = AuditContext(
            conversation_excerpt=cdata.get("conversation_excerpt", ""),
            cited_files=cdata.get("cited_files", []),
        )

    broker = Broker()
    try:
        resp = asyncio.run(broker.propose_intent(payload, ctx))
    except BrokerError as e:
        console.print(f"[red]broker error:[/] {e}")
        sys.exit(2)
    click.echo(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2))


@cli.command("status")
@click.argument("intent_id")
def status_cmd(intent_id: str) -> None:
    """Show current state for an intent."""
    broker = Broker()
    data = broker.status(intent_id)
    if data is None:
        console.print(f"[red]no intent[/] {intent_id}")
        sys.exit(1)
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


@cli.command("history")
@click.option("--limit", type=int, default=20)
@click.option(
    "--format",
    "output",
    type=click.Choice(["pretty", "json"]),
    default="pretty",
)
def history_cmd(limit: int, output: str) -> None:
    """Recent intents and outcomes."""
    broker = Broker()
    rows = broker.history(limit=limit)
    if output == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        console.print("[dim]no intents yet.[/]")
        return

    table = Table(title=f"Last {len(rows)} intents", show_lines=False)
    table.add_column("intent_id", style="cyan", overflow="fold")
    table.add_column("merchant")
    table.add_column("amount", justify="right")
    table.add_column("tier")
    table.add_column("state")
    table.add_column("created_at")
    for r in rows:
        state = r["current_state"]
        state_style = {
            "settled": "green",
            "rejected": "red",
            "failed": "red",
            "user_declined": "yellow",
            "playbook_broken": "red",
            "executing": "blue",
            "awaiting_user": "yellow",
            "audited": "blue",
            "proposed": "dim",
        }.get(state, "white")
        table.add_row(
            r["intent_id"][:8],
            r["merchant"],
            f"${r['amount_usd']:.2f}",
            r["tier"],
            f"[{state_style}]{state}[/]",
            r["created_at"],
        )
    console.print(table)


@cli.command("check-balance")
def check_balance_cmd() -> None:
    """MetaMask balance + vast credit + spending caps."""
    broker = Broker()
    data = broker.check_balance()
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


@cli.command("resume")
@click.argument("intent_id")
def resume_cmd(intent_id: str) -> None:
    """Resume an intent that was awaiting user signature (run after MetaMask confirm)."""
    broker = Broker()
    try:
        resp = asyncio.run(broker.resume_awaiting_user(intent_id))
    except BrokerError as e:
        console.print(f"[red]broker error:[/] {e}")
        sys.exit(2)
    click.echo(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2))


@cli.command("analyze-audits")
@click.option("--since", type=str, default=None, help="ISO date (YYYY-MM-DD) lower bound")
@click.option(
    "--format",
    "output",
    type=click.Choice(["pretty", "json", "csv"]),
    default="pretty",
)
def analyze_audits_cmd(since: str | None, output: str) -> None:
    """A/B comparison of Codex vs Claude verdicts (requires shadow mode data)."""
    ledger = Ledger()
    since_iso = None
    if since:
        since_iso = f"{since}T00:00:00Z"
    rows = ledger.audit_comparison(since_iso=since_iso)

    if output == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    if output == "csv":
        import csv

        writer = csv.writer(sys.stdout)
        if rows:
            writer.writerow(rows[0].keys())
            for r in rows:
                writer.writerow(r.values())
        return

    if not rows:
        console.print("[dim]no audit data[/]")
        return

    both = [r for r in rows if r["codex_verdict"] and r["claude_verdict"]]
    agree = sum(1 for r in both if r["codex_verdict"] == r["claude_verdict"])
    console.print(
        f"[bold]Audit comparison:[/] {len(rows)} intents, "
        f"{len(both)} with both auditors, {agree}/{len(both)} agree "
        f"({100 * agree / max(1, len(both)):.1f}%)"
    )

    table = Table(title="Codex vs Claude verdicts")
    table.add_column("intent_id", style="cyan")
    table.add_column("amount", justify="right")
    table.add_column("codex")
    table.add_column("claude")
    table.add_column("created")
    for r in rows:
        c_v = r["codex_verdict"] or "—"
        cl_v = r["claude_verdict"] or "—"
        style = "green" if c_v == cl_v and c_v != "—" else "yellow"
        table.add_row(
            r["intent_id"][:8],
            f"${r['amount_usd']:.2f}",
            f"[{style}]{c_v}[/]",
            f"[{style}]{cl_v}[/]",
            r["created_at"],
        )
    console.print(table)


@cli.command("export-logs")
@click.argument("outfile", type=click.Path(dir_okay=False, path_type=Path))
def export_logs_cmd(outfile: Path) -> None:
    """Dump the full ledger as JSON (for debugging and research output)."""
    ledger = Ledger()
    payload = {
        "intents": ledger.list_intents(limit=10_000),
        "audit_comparison": ledger.audit_comparison(),
    }
    outfile.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    console.print(f"[green]wrote[/] {outfile}")


@cli.command("email-lock")
@click.argument("address", required=False)
@click.option("--show", is_flag=True, help="Show the currently locked email.")
@click.option("--reset", is_flag=True, help="Reset the lock (requires confirmation).")
def email_lock_cmd(address: str | None, show: bool, reset: bool) -> None:
    """Set (write-once) or inspect the broker's confirmation email.

    Usage:
        broker email-lock warrenzhong666@gmail.com    # initial lock
        broker email-lock --show                       # inspect
        broker email-lock --reset                      # wipe (will prompt to confirm)
    """
    if show:
        try:
            email = load_locked_email()
        except EmailLockError as e:
            console.print(f"[red]tampered:[/] {e}")
            sys.exit(2)
        if email is None:
            console.print("[yellow]no email is locked yet[/]")
            return
        console.print(f"[green]locked:[/] {email}")
        return

    if reset:
        console.print(
            "[yellow]warning:[/] resetting the lock invalidates the OTP channel. "
            "Any in-flight intents may succeed even though a malicious agent could "
            "have rerouted their OTPs. Only reset when you are certain this is "
            "what you want."
        )
        if not click.confirm("Proceed with reset?", default=False):
            console.print("cancelled.")
            return
        reset_lock("I_UNDERSTAND_THIS_INVALIDATES_PAST_INTENTS")
        console.print("[green]lock cleared.[/]")
        return

    if not address:
        console.print("supply an email address, or use --show / --reset")
        sys.exit(1)
    try:
        lock = lock_email(address)
    except EmailLockViolation as e:
        console.print(f"[red]violation:[/] {e}")
        sys.exit(2)
    except EmailLockError as e:
        console.print(f"[red]error:[/] {e}")
        sys.exit(2)
    console.print(f"[green]locked[/] {lock.email} at {lock.locked_at}")


@cli.command("demo")
@click.option(
    "--merchant", default="openrouter.ai", help="Merchant to simulate a topup for."
)
@click.option("--amount", type=float, default=1.0, help="Dollar amount.")
@click.option(
    "--with-popup/--no-popup",
    default=False,
    help=(
        "With --with-popup, actually opens the local popup window so you can see it. "
        "Default --no-popup just runs the pipeline start-to-finish without blocking."
    ),
)
def demo_cmd(merchant: str, amount: float, with_popup: bool) -> None:
    """Zero-config end-to-end dry-run. No real money, no API keys, no browser.

    Sets KYA_BROKER_DRY_RUN + friends automatically and runs a fake intent
    through the full pipeline (audit → optional email-OTP popup → execution
    → ledger). Useful for: verifying the install works, onboarding a new
    machine, or exercising the popup window locally.

    The email used is always "demo@example.com"; real runs use the address
    the user locks via `broker email-lock <addr>`.
    """
    import asyncio
    import os

    os.environ.setdefault("KYA_BROKER_DRY_RUN", "1")
    os.environ.setdefault("KYA_BROKER_DRY_RUN_AUDITOR", "approve")
    os.environ.setdefault("KYA_BROKER_DRY_RUN_OUTCOME", "settled")
    os.environ.setdefault("KYA_BROKER_DRY_RUN_HUMAN_GATE", "completed")
    os.environ.setdefault("KYA_BROKER_OTP_SHOW_IN_TERMINAL", "1")

    if not with_popup:
        os.environ["KYA_BROKER_SMOKE_SKIP_OTP"] = "1"

    # Demo needs an email-lock; auto-lock a demo placeholder if none yet.
    locked = load_locked_email()
    if locked is None:
        lock_email("demo@example.com")
        console.print(
            "[dim]no email lock found in this install → auto-locked demo@example.com "
            "for this run (real use should lock your actual email via "
            "`broker email-lock <addr>`)[/]"
        )
    else:
        console.print(f"[dim]using locked email: {locked}[/]")

    # Demo also needs at least one enrolled payment method. If none, inject
    # a demo card so rail selection can succeed.
    from .config import PaymentMethod, load_config, save_config

    cfg = load_config()
    if not cfg.payment_methods:
        cfg.payment_methods.append(
            PaymentMethod(
                name="demo card",
                rail="card",
                last4="4242",
                notes="auto-enrolled by broker demo; not a real card",
            )
        )
        if "card" not in cfg.rails:
            cfg.rails.insert(0, "card")
        save_config(cfg)
        console.print(
            "[dim]no payment methods enrolled → auto-added a 'demo card' for the "
            "card rail. Real use: run `broker setup` to enroll your actual methods.[/]"
        )

    if with_popup:
        console.print(
            "[yellow]--with-popup is set.[/] A browser tab will open with the "
            "OTP popup; the code is printed above (SHOW_IN_TERMINAL=1). Paste it "
            "to complete the demo, or click Decline to see the decline path."
        )

    payload = {
        "merchant": merchant,
        "amount_usd": amount,
        "rationale": (
            f"demo run — no real money moves. Simulating a ${amount:.2f} topup at "
            f"{merchant} to verify the broker pipeline works end-to-end."
        ),
        "estimated_actual_cost_usd": amount,
    }
    broker = Broker()
    console.print(f"[bold]demo:[/] proposing {json.dumps(payload, ensure_ascii=False)}")
    try:
        resp = asyncio.run(
            broker.propose_intent(
                payload,
                AuditContext(
                    conversation_excerpt=(
                        "User ran `broker demo` to exercise the pipeline. No real "
                        "payment intent — auditor is mocked, human gate is mocked, "
                        "rail execution is mocked."
                    ),
                ),
            )
        )
    except BrokerError as e:
        console.print(f"[red]broker error:[/] {e}")
        sys.exit(2)
    click.echo(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2))


def main() -> None:
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
