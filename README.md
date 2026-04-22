# KYA-Broker

**A generic agent-payment skill for Claude Code.** Clone it into `~/.claude/skills/`, run one setup wizard, and your Claude Code can top up whatever merchant you allowlist — **OpenRouter, vast.ai, Anthropic, and anything else with a playbook** — using your existing human payment methods (credit card, MetaMask, email-authorised accounts). The skill drives the browser up to the card / wallet / OTP step, then hands over and waits. It never sees card numbers, passwords, or private keys.

- ✦ **Rail-agnostic.** Credit card (Stripe / Chrome autofill / 1Password / Apple Pay), crypto (MetaMask USDC), email magic links, 3D-Secure challenges, SMS OTP — all reduce to the same "HumanGate" primitive.
- ✦ **Portable.** macOS / Linux + Python 3.11+ + Chrome. No per-user server, no device-bound keys.
- ✦ **Safe by default.** Independent Codex auditor reviews every intent; cross-model-family audit hedges against Claude-auditing-Claude shared biases. Shadow mode runs Codex and Claude in parallel for A/B research.
- ✦ **Observable.** SQLite ledger with full state history, audit verdicts, tx hashes and receipts. Export via `broker export-logs`.
- ✦ **Research-ready.** Shadow mode gives you Codex-vs-Claude verdict data on real intents via `broker analyze-audits`.

> **Status:** v0.4 — generic payment rails. Previous v0.3.1 was MetaMask/vast-only; this release lifts the HumanGate abstraction to cover credit cards, 3DS, email links, and MetaMask uniformly. 51 tests passing.

## 5-minute quickstart

```bash
# 1. Clone into the Claude Code skills directory
git clone <this-repo> ~/.claude/skills/kya-broker
cd ~/.claude/skills/kya-broker

# 2. Install deps + wrapper scripts
bash install.sh

# 3. Make sure ~/.local/bin is on PATH
export PATH="$HOME/.local/bin:$PATH"

# 4. Run the wizard (audit layer, enroll methods, allowlist merchants, thresholds)
broker setup

# 5. Smoke-test
broker check-balance
```

The `kya-broker` skill auto-registers when the repo lives at `~/.claude/skills/kya-broker/` (Claude Code reads `SKILL.md`).

## Architecture at a glance

```
┌──────────────────┐  propose_intent  ┌─────────────────────┐
│  Claude Code     │ ───────────────▶ │   broker (Python)   │
│  (agent session) │                  │  intent + ledger    │
└──────────────────┘                  └──────────┬──────────┘
                                                 │ audit
                                                 ▼
                                       ┌─────────────────────┐
                                       │ Codex  (primary)    │ ← independent
                                       │ Claude (shadow/fb)  │   model family
                                       └──────────┬──────────┘
                                                  │ approved + L0/L1 gate
                                                  ▼
                                       ┌─────────────────────┐
                                       │ Chrome (user's)     │
                                       │ ── navigate, fill   │
                                       │ ── HumanGate ──────┐│
                                       └──────────┬──────────┘│
                                                  │           │
                              (card / MetaMask / email / OTP) │
                                                  │           │
                                                  ◀───────────┘
                                                  │ receipt + tx hash
                                                  ▼
                                       ┌─────────────────────┐
                                       │ merchant credit +$N │
                                       │  (OpenRouter / vast │
                                       │   / Anthropic …)    │
                                       └─────────────────────┘
```

## CLI reference

| Command | What it does |
|---|---|
| `broker setup` | Interactive wizard: audit, payment-method enrollment, merchant allowlist, thresholds |
| `broker propose-intent intent.json [--context-file ctx.json]` | Submit an intent (same schema as the MCP tool) |
| `broker status <intent_id>` | State machine + audits + execution + human-gate history |
| `broker check-balance` | Spending caps + Chrome-attached wallet balance |
| `broker history [--limit N] [--format pretty|json]` | Recent intents |
| `broker resume <intent_id>` | Resume after a human-gate in awaiting_user |
| `broker analyze-audits [--since YYYY-MM-DD]` | Codex vs Claude verdicts (shadow mode) |
| `broker export-logs out.json` | Dump the full ledger for offline research |

## Payment methods

Declared in `config.yaml`:

```yaml
payment_methods:
  - name: "research visa"
    rail: card
    last4: "4242"
    notes: "chrome autofill profile 'work'"
    max_auto_execute_usd: 20.00

  - name: "metamask main"
    rail: crypto
    wallet_address: "0xabc…def"
    notes: "MetaMask · Polygon · USDC"

  - name: "email magic-link"
    rail: email_link
    notes: "warrenzhong666@gmail.com"
```

The skill stores only labels / last-4 / wallet-address — never full card numbers or seeds. The actual fill / sign happens in the browser, out of the skill's reach.

## Merchants shipped with defaults

| Merchant | Rails | Playbook(s) |
|---|---|---|
| openrouter.ai | card, crypto | `openrouter_topup_card.yaml`, `openrouter_topup_crypto.yaml` |
| vast.ai | crypto, card | `vast_topup_crypto.yaml`, `vast_topup_card.yaml` |
| anthropic.com | card | `anthropic_topup_card.yaml` |

Add your own with [docs/playbook_authoring.md](docs/playbook_authoring.md). Each new merchant is a YAML file + an entry in `config.yaml` → `merchants:`.

## Audit layer

```yaml
audit:
  primary: codex | claude | auto
  shadow_mode: false
  fallback_on_primary_failure: false
  timeout_seconds: 30
```

Codex is recommended as primary because it's from a different model family than Claude Code; same-family audit (Claude audits Claude) shares training biases. Shadow mode runs both auditors on every intent and writes both verdicts to the ledger for A/B analysis.

## The L0 / L1 / L2 gate

```
L0  amount ≤ l0_ceiling_usd  → audit only, auto-execute (rail gate still runs)
L1  amount ≤ l1_ceiling_usd  → audit + explicit human gate (card/sign/OTP)
L2  amount  > l1_ceiling_usd → broker refuses; ask human out of band
```

Note: even at L0, card / crypto / email_link rails always require a human gate at the rail level (you still have to type the CVV or sign the tx). L0 just means the broker doesn't block on an additional confirmation step beyond the rail's own.

## FAQ

**Does this store my credit card?** No. The card number is typed into the merchant's Stripe iframe by you (or autofilled from Chrome / 1Password / Apple Pay). The broker sees only last-4 / expiry via DOM inspection after payment, if at all.

**Does this touch my MetaMask seed phrase?** No. MetaMask unlocks with your password inside the extension; the broker never sees it.

**What if a merchant adds 2FA mid-checkout?** Most flows (3DS, SMS OTP, email link) are already modeled as optional HumanGates. The broker detects them, fires a gate, waits. If you hit a novel gate we don't recognise, it'll timeout and mark `playbook_broken` — file a PR with a new `wait_for_human` step.

**Can I use saved cards / Apple Pay / Google Pay?** Yes. The broker doesn't care how the card field gets filled — browser autofill, 1Password, Apple Pay, manual typing are all fine from its perspective. The completion detector just watches for success text.

**Can two machines share one card?** Yes, but be careful with duplicate charges. Each intent creates one charge at its own merchant; there's no shared state between machines, so don't auto-propose the same intent from two machines simultaneously.

**Uninstall?** `bash uninstall.sh` removes wrappers. Ledger + config stay in `~/.claude/skills/kya-broker.local/` — delete that folder to wipe history.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/   # ~51 tests, ~1.5s
```

Tests run in a tmpdir sandbox (`tests/conftest.py`) and force `KYA_BROKER_DRY_RUN=1` — no browser, no real API calls.

## License

MIT. See [LICENSE](LICENSE).
