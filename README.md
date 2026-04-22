# KYA-Broker

**An autonomous payment skill for Claude Code.** Clone it into `~/.claude/skills/`, run one setup wizard, and your Claude Code can handle the whole "top up vast.ai → launch a GPU box → reproduce the paper" loop on its own. It never sees your wallet password — final signatures stay inside your MetaMask browser extension.

- ✦ **Portable.** Any macOS or Linux box with Python 3.11+, Chrome, and MetaMask can install the skill. No per-user server, no device-bound keys.
- ✦ **Safe by default.** Every intent is cross-audited by Codex (or Claude, or both) before a dollar moves. Large intents trigger a native MetaMask popup — auto-clicking Confirm is a deliberate non-feature.
- ✦ **Observable.** SQLite ledger with full state history, audit verdicts, and tx hashes. Export to JSON for analysis with `broker export-logs`.
- ✦ **Research-ready.** Shadow mode runs Codex and Claude in parallel on the same intent so you can collect A/B verdict data from real usage.

> **Status:** v0.3.1 — dual-auditor + MetaMask-native authorization. The intent lifecycle, ledger, auditor, and CLI/MCP surface are feature-complete; Chrome automation is in place with a dry-run simulator for development and a CDP backend for real runs. See [dev plan](docs/architecture.md) for milestones.

## 5-minute quickstart

```bash
# 1. Clone into the Claude Code skills directory
git clone <this-repo> ~/.claude/skills/kya-broker
cd ~/.claude/skills/kya-broker

# 2. Install deps + wrapper scripts
bash install.sh

# 3. Make sure ~/.local/bin is on PATH
export PATH="$HOME/.local/bin:$PATH"

# 4. Run the wizard (audit layer, policy, vast.ai, funding)
broker setup

# 5. Smoke-test
broker check-balance
```

Then in Claude Code, the `kya-broker` skill is auto-discovered (SKILL.md in the repo). When you ask Claude to reproduce a paper that needs GPUs, it'll call `broker propose-intent` on its own.

## Architecture at a glance

```
┌──────────────────┐       propose_intent (MCP)        ┌─────────────────────┐
│  Claude Code     │ ────────────────────────────────▶ │   broker (Python)   │
│  (agent session) │                                    │  intent + ledger    │
└──────────────────┘                                    └──────────┬──────────┘
                                                                   │ audit
                                                                   ▼
                                                        ┌─────────────────────┐
                                                        │ Codex (primary)     │  ← independent
                                                        │ Claude (shadow/fb)  │    model family
                                                        └──────────┬──────────┘
                                                                   │ approved + L0/L1 gate
                                                                   ▼
                                                        ┌─────────────────────┐
                                                        │ Chrome + MetaMask   │
                                                        │  (user's browser)   │
                                                        └──────────┬──────────┘
                                                                   │ tx hash + receipt
                                                                   ▼
                                                        ┌─────────────────────┐
                                                        │  vast.ai credit     │
                                                        └─────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for the full narrative.

## CLI reference

| Command | What it does |
|---|---|
| `broker setup` | Interactive first-run wizard (audit layer, vast.ai, funding, thresholds) |
| `broker propose-intent intent.json` | Submit a payment intent (same semantics as the MCP tool) |
| `broker status <intent_id>` | Poll state machine + audits + execution |
| `broker history [--limit N] [--format pretty|json]` | Recent intents |
| `broker check-balance` | MetaMask USDC + spending caps |
| `broker resume <intent_id>` | Resume a L1 intent after the user signs in MetaMask |
| `broker analyze-audits [--since YYYY-MM-DD] [--format pretty|json|csv]` | Codex-vs-Claude A/B comparison |
| `broker export-logs out.json` | Dump the ledger for offline research |

## Configuring the audit layer

In `~/.claude/skills/kya-broker.local/config.yaml`:

```yaml
audit:
  primary: codex | claude | auto   # auto = codex if available, else claude
  shadow_mode: false               # true = run the other auditor in parallel (no veto)
  fallback_on_primary_failure: false
  timeout_seconds: 30
```

**Why Codex is the recommended primary.** A Claude-auditing-Claude pipeline shares training biases; the same jailbreak might slip past both. Codex (a GPT family model via OpenAI) is from a different model family, which is the cross-source-review equivalent of a second keypair on a multisig.

**Shadow mode** runs both auditors on every intent but only the primary's verdict gates execution. This doubles your audit cost (~$0.004–$0.02/intent) but gives you two verdicts per intent for comparison via `broker analyze-audits`. Use this if you're researching multi-auditor effectiveness.

## The L0 / L1 / L2 gate

```
L0  amount <= l0_ceiling_usd  →  audit only, auto-execute on approve
L1  amount <= l1_ceiling_usd  →  audit + native MetaMask popup, user signs
L2  amount  > l1_ceiling_usd  →  broker refuses; you must approve out of band
```

`l0_ceiling_usd` and `l1_ceiling_usd` are in your config. Defaults are $2 and $50 respectively — tune to your risk tolerance.

## FAQ

**Does this touch my MetaMask seed phrase?** No. The skill drives Chrome, which drives the MetaMask extension. You unlock MetaMask with your password; the skill never sees it and cannot sign on your behalf.

**What if vast.ai changes their UI?** The playbook (`playbooks/vast_topup_crypto.yaml`) captures every selector/step. When a step fails the broker dumps the DOM + screenshot to `~/.claude/skills/kya-broker.local/dumps/` and marks the intent `playbook_broken`. Fix is "update the YAML, PR" — no Python changes needed.

**Can I use this on two machines at once with the same wallet?** No — not safely. Two brokers signing with the same address will race. If you need multi-machine, use a different MetaMask account per machine.

**How do I uninstall?** `bash uninstall.sh` removes wrappers + venv. Your ledger, config, and `.env` stay in `~/.claude/skills/kya-broker.local/` — delete that folder if you also want to wipe history.

**Is this going to get my vast.ai account banned?** vast's ToS likely forbid bot-driven checkouts. At low volume (< 20 intents/month per account) this hasn't been observed, but assume the risk is real. The skill is not a good fit for high-throughput or commercial automation.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/    # 48 tests, ~1s
```

Tests run in a tmpdir sandbox (see `tests/conftest.py`) and force `KYA_BROKER_DRY_RUN=1` so no real browser is needed.

## License

MIT. See [LICENSE](LICENSE).
