"""Interactive first-run wizard.

Covers:
  1. Prerequisites (Python, Chrome, MetaMask)
  2. Auditor selection (Codex, Claude, or both in shadow mode)
  3. vast.ai login check
  4. MetaMask funding check
  5. Policy thresholds
  6. Dry-run smoke test
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, Prompt

from .config import Config, load_config, save_config
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

    console.print()
    console.print(
        Panel.fit(
            "Make sure MetaMask is installed in the Chrome profile you'll use with "
            "this skill. It's the browser-extension wallet at https://metamask.io.\n\n"
            "If you haven't installed it, do that now — the skill will drive a Chrome "
            "window that assumes MetaMask is present.",
            title="MetaMask",
            border_style="cyan",
        )
    )
    if not Confirm.ask("Do you have MetaMask installed in Chrome?", default=True):
        warn("Please install MetaMask, then rerun `broker setup`.")
        all_ok = False

    return all_ok


def configure_auditor(cfg: Config) -> None:
    console.print(
        Panel.fit(
            "[bold]Auditor[/] is the second opinion that reviews every payment intent "
            "before the broker spends money. Using Codex (OpenAI) is strongly recommended "
            "— a Claude auditor reviewing a Claude Code agent shares training biases, "
            "and some prompt injections that slip past one may slip past the other.\n\n"
            "Option A: Codex (OpenAI)  — recommended primary\n"
            "Option B: Claude (Anthropic) — fallback (same-family, weaker)\n"
            "Option C: Both (shadow-mode research) — Codex decides, Claude runs in parallel",
            title="Step 2 — Configure audit layer",
            border_style="cyan",
        )
    )
    choice = Prompt.ask("Your choice", choices=["A", "B", "C"], default="A")

    codex_ok = bool(shutil.which("codex"))
    if choice in ("A", "C"):
        if not codex_ok:
            warn("`codex` CLI not found on PATH.")
            if Confirm.ask(
                "Install codex CLI now via pip? (you can also install manually later)",
                default=False,
            ):
                subprocess.run([sys.executable, "-m", "pip", "install", "codex-cli"], check=False)
                codex_ok = bool(shutil.which("codex"))

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            entered = Prompt.ask(
                "Paste your OpenAI API key (or press Enter to skip and use `codex login` later)",
                default="",
                password=True,
            )
            if entered:
                _append_env("OPENAI_API_KEY", entered)
                os.environ["OPENAI_API_KEY"] = entered
        if not codex_ok:
            warn("Codex will be unavailable until you install the CLI and configure auth.")

    if choice in ("B", "C"):
        anth_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anth_key:
            entered = Prompt.ask(
                "Paste your Anthropic API key",
                default="",
                password=True,
            )
            if entered:
                _append_env("ANTHROPIC_API_KEY", entered)
                os.environ["ANTHROPIC_API_KEY"] = entered

    cfg.audit.primary = {"A": "codex", "B": "claude", "C": "codex"}[choice]
    cfg.audit.shadow_mode = choice == "C"

    if choice == "B":
        warn(
            "Claude-auditing-Claude has shared-bias risk. Consider configuring Codex later "
            "via `broker setup --audit-only`."
        )
    ok(
        f"audit.primary = {cfg.audit.primary}, shadow_mode = {cfg.audit.shadow_mode}"
    )


def configure_vast(_cfg: Config) -> None:
    console.print(
        "Open https://vast.ai in the same Chrome profile you'll use with this skill and "
        "log in. The skill doesn't store your vast password — it just reuses an existing "
        "logged-in browser session."
    )
    Prompt.ask("Press Enter once you're logged into vast.ai", default="")
    ok("assuming vast.ai login is active")


def configure_funding(_cfg: Config) -> None:
    console.print(
        Panel.fit(
            "You need USDC in your MetaMask wallet. For this skill we default to Polygon\n"
            "USDC because gas is cheap and settlement is fast.\n\n"
            "If you're starting from zero:\n"
            "  1. Buy USDC on an exchange (OKX / Coinbase / Binance)\n"
            "  2. Withdraw USDC on Polygon network to your MetaMask address\n"
            "  3. Verify the balance shows up in your MetaMask extension\n\n"
            "For a $10 topup you want at least ~$15 in the wallet to cover future runs.",
            title="Step 4 — Fund MetaMask with USDC",
            border_style="cyan",
        )
    )
    if not Confirm.ask("Is your MetaMask funded with USDC?", default=True):
        warn("Fund your wallet before using the skill. Setup will continue with remaining steps.")


def configure_policy(cfg: Config) -> None:
    console.print(
        "[bold]Spending thresholds[/] decide when audit + user prompts kick in:\n"
        "  L0 (<= l0_ceiling): audit runs, auto-execute on approve (no popup)\n"
        "  L1 (l0 < amount <= l1): audit runs, MetaMask popup asks you to sign\n"
        "  L2 (> l1_ceiling): broker refuses; you must approve out of band.\n"
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


def smoke_test(cfg: Config) -> bool:
    from .auditor import select_auditor

    try:
        primary, shadow = select_auditor(cfg)
        ok(f"audit primary = {primary.name}, shadow = {[s.name for s in shadow] or '[]'}")
    except Exception as e:  # noqa: BLE001
        fail(f"audit setup: {e}")
        return False

    # Init ledger
    init_ledger()
    ok(f"ledger initialised at {local_root() / 'ledger.sqlite'}")

    # Dry-run topup
    os.environ["KYA_BROKER_DRY_RUN"] = "1"
    os.environ["KYA_BROKER_DRY_RUN_OUTCOME"] = "settled"
    import asyncio

    from .auditor.base import AuditContext
    from .broker import Broker

    async def _run() -> bool:
        broker = Broker()
        resp = await broker.propose_intent(
            {
                "merchant": "vast.ai",
                "amount_usd": 0.5,
                "rationale": "dry-run smoke test for setup wizard — no real money",
                "estimated_actual_cost_usd": 0.5,
            },
            AuditContext(
                conversation_excerpt=(
                    "User is running the KYA-Broker setup wizard and asked for a dry-run "
                    "smoke test to confirm the broker can propose, audit, and execute a "
                    "small intent end-to-end."
                ),
            ),
        )
        return resp.state in {"settled", "rejected"}  # reject is acceptable (auditor may err cautious)

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
    # Ensure default merchants list populated if empty
    if not cfg.merchants:
        raw = yaml.safe_load(default_policy_path().read_text(encoding="utf-8"))
        cfg.merchants = [
            _merchant_from_dict(m) for m in raw.get("merchants", [])
        ]
    save_config(cfg)
    ok(f"config written to {local_root() / 'config.yaml'}")


def _merchant_from_dict(d: dict):
    from .config import MerchantConfig

    return MerchantConfig(
        name=d["name"],
        playbook=d["playbook"],
        max_single_topup_usd=float(d.get("max_single_topup_usd", 50.0)),
        preferred_rail=d.get("preferred_rail", "crypto"),
    )


def _append_env(key: str, value: str) -> None:
    p = env_path()
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    lines = [ln for ln in existing.splitlines() if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]KYA-Broker setup[/]\n"
            "This wizard takes about 10-15 minutes. You can rerun it any time.",
            border_style="cyan",
        )
    )

    step(1, 6, "Check prerequisites")
    if not check_prereqs():
        warn("prerequisites not fully satisfied; continue anyway? (y/N)")
        if not Confirm.ask("Continue?", default=False):
            sys.exit(1)

    # Ensure we have a config to edit (load default if none yet)
    try:
        cfg = load_config()
    except Exception:
        # Copy default policy into place for the first run
        default_policy_path_ = default_policy_path()
        raw = default_policy_path_.read_text(encoding="utf-8")
        (local_root() / "config.yaml").write_text(raw, encoding="utf-8")
        cfg = load_config()

    step(2, 6, "Configure audit layer")
    configure_auditor(cfg)

    step(3, 6, "Confirm vast.ai login")
    configure_vast(cfg)

    step(4, 6, "Fund MetaMask")
    configure_funding(cfg)

    step(5, 6, "Set spending policy")
    configure_policy(cfg)

    write_config(cfg)

    step(6, 6, "Smoke test")
    if smoke_test(cfg):
        console.print(Panel.fit("[bold green]Setup complete.[/]", border_style="green"))
    else:
        console.print(
            Panel.fit(
                "[yellow]Setup mostly complete, but smoke test did not pass.[/]\n"
                "Re-run `broker setup` after fixing the warnings above.",
                border_style="yellow",
            )
        )


if __name__ == "__main__":
    main()
