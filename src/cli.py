"""`broker` CLI for v1.0 — thin ledger + budget interface.

This CLI does NOT drive the browser. That is the agent's job, using the
Claude-in-Chrome MCP tools described in SKILL.md. The CLI exists only so the
agent (and the user) can:

  * Record what the agent attempted (and what it settled to)
  * Enforce per-day / per-month spending caps
  * View history

If you don't install this CLI, the skill still works — you just lose the
ledger and the cap enforcement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .ledger import Ledger, LedgerError

console = Console()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="broker")
def cli() -> None:
    """KYA-Broker v1.0: thin ledger for browser-native agent payments.

    The browser driving happens in the agent's chat session via the
    Claude-in-Chrome MCP. This CLI only tracks intents + spending caps.
    """


@cli.command("log")
@click.option("--merchant", required=True)
@click.option("--amount", type=float, required=True)
@click.option("--rationale", default=None, help="Short reason; surfaces in history.")
@click.option(
    "--status",
    type=click.Choice(["proposed", "settled", "failed", "declined", "cancelled"]),
    default="proposed",
)
@click.option("--note", default=None)
def log_cmd(
    merchant: str, amount: float, rationale: str | None, status: str, note: str | None
) -> None:
    """Log a payment intent. Prints the new intent_id."""
    try:
        intent_id = Ledger().log_intent(
            merchant=merchant,
            amount_usd=amount,
            rationale=rationale,
            status=status,
            note=note,
        )
    except LedgerError as e:
        console.print(f"[red]error:[/] {e}")
        sys.exit(2)
    click.echo(intent_id)


@cli.command("update")
@click.argument("intent_id")
@click.option(
    "--status",
    type=click.Choice(["proposed", "settled", "failed", "declined", "cancelled"]),
    default=None,
)
@click.option("--note", default=None)
def update_cmd(intent_id: str, status: str | None, note: str | None) -> None:
    """Update an existing intent's status / note."""
    try:
        Ledger().update_intent(intent_id, status=status, note=note)
    except LedgerError as e:
        console.print(f"[red]error:[/] {e}")
        sys.exit(2)
    console.print(f"[green]updated[/] {intent_id}")


@cli.command("history")
@click.option("--limit", type=int, default=20)
@click.option(
    "--format",
    "output",
    type=click.Choice(["pretty", "json"]),
    default="pretty",
)
def history_cmd(limit: int, output: str) -> None:
    """Recent intents, newest first."""
    rows = Ledger().list_intents(limit=limit)
    if output == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    if not rows:
        console.print("[dim]no intents yet[/]")
        return
    table = Table(title=f"Last {len(rows)} intents", show_lines=False)
    table.add_column("intent_id", style="cyan", overflow="fold")
    table.add_column("merchant")
    table.add_column("amount", justify="right")
    table.add_column("status")
    table.add_column("created")
    for r in rows:
        style = {
            "settled": "green",
            "failed": "red",
            "declined": "yellow",
            "cancelled": "dim",
            "proposed": "blue",
        }.get(r["status"], "white")
        table.add_row(
            r["intent_id"][:8],
            r["merchant"],
            f"${r['amount_usd']:.2f}",
            f"[{style}]{r['status']}[/]",
            r["created_at"],
        )
    console.print(table)


@cli.command("budget")
@click.option("--daily", "daily_cap", type=float, default=None,
              help="Set daily spending cap (USD).")
@click.option("--monthly", "monthly_cap", type=float, default=None,
              help="Set monthly spending cap (USD).")
def budget_cmd(daily_cap: float | None, monthly_cap: float | None) -> None:
    """Get or set spending caps. Both caps are optional; unset = no cap."""
    led = Ledger()
    if daily_cap is not None:
        led.set_budget("daily_cap_usd", daily_cap)
    if monthly_cap is not None:
        led.set_budget("monthly_cap_usd", monthly_cap)

    budget = led.get_budget()
    spent_24h = led.spent_within_hours(24)
    spent_30d = led.spent_within_hours(24 * 30)

    table = Table(show_lines=False)
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("daily cap", f"${budget.get('daily_cap_usd', 0):.2f}" if "daily_cap_usd" in budget else "[dim]unset[/]")
    table.add_row("spent (last 24h)", f"${spent_24h:.2f}")
    table.add_row("monthly cap", f"${budget.get('monthly_cap_usd', 0):.2f}" if "monthly_cap_usd" in budget else "[dim]unset[/]")
    table.add_row("spent (last 30d)", f"${spent_30d:.2f}")
    console.print(table)


@cli.command("check-budget")
@click.argument("amount", type=float)
def check_budget_cmd(amount: float) -> None:
    """Exit 0 if `amount` fits in remaining caps; non-zero with message if not.

    Designed for the agent to call before any money-moving click:
        broker check-budget 5 || { echo "abort"; exit 1; }
    """
    ok, reason = Ledger().check_budget(amount)
    if ok:
        console.print(f"[green]ok[/] ${amount:.2f} fits within remaining caps")
        sys.exit(0)
    console.print(f"[red]abort:[/] {reason}")
    sys.exit(1)


@cli.command("export")
@click.argument("outfile", type=click.Path(dir_okay=False, path_type=Path))
def export_cmd(outfile: Path) -> None:
    """Dump all intents + budget as JSON."""
    led = Ledger()
    payload = {
        "intents": led.list_intents(limit=10_000),
        "budget": led.get_budget(),
    }
    outfile.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    console.print(f"[green]wrote[/] {outfile}")


def main() -> None:
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
