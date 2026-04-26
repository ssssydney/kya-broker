# KYA-Broker v1.0 — browser-native agent payment skill

**A Claude Code skill that lets agents complete payment workflows on any merchant the user has previously interacted with.** It does this by driving the user's Chrome via the Claude-in-Chrome MCP — using saved passwords, autofilled cards, and saved checkout methods that the user has already approved. The skill never touches card numbers, passwords, or 3DS / OTP codes; those are the user's exclusively, handled by Chrome and the merchant's checkout.

> **Status:** v1.0 — radically simplified. v0.5 had its own audit layer + email lock + SMTP popup + per-merchant playbooks (~3500 lines of Python). v1.0 trusts Chrome + payment processors instead and is ~300 lines. v0.5 is preserved at git tag `v0.5` for reference.

## Quickstart

```bash
# One-line install
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"

# Drop SKILL.md into Claude Code's skills directory
mkdir -p ~/.claude/skills/kya-broker
curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/SKILL.md \
  -o ~/.claude/skills/kya-broker/SKILL.md
```

Required: Python 3.11+, Chrome with the **Claude for Chrome** extension installed and signed in.

That's it. No SMTP, no email lock, no API keys, no setup wizard.

## How it works

```
Claude Code (agent)
    │
    │ "user wants to top up vast.ai $5"
    │
    ├── confirm intent in chat with user ──→ user says "go"
    │
    ├── (optional) broker check-budget 5    ──→ ok / abort
    ├── (optional) broker log --merchant vast.ai --amount 5 ──→ intent_id
    │
    ├── Claude-in-Chrome MCP drives browser
    │     navigate → fill amount → pick saved card → screenshot
    │
    ├── show screenshot to user, ask "go?" ──→ user says "go"
    │
    ├── click Submit
    │
    ├── handle 3DS / OTP if it appears ──→ "complete in browser, I'll wait"
    │
    ├── verify settlement page
    │
    └── (optional) broker update <intent_id> --status settled
```

Browser does the work. User has the final say at every money-moving moment. Skill is a coordinator, not a payment processor.

## Why v1.0 ditched v0.5's architecture

v0.5 added a Codex/Claude cross-model audit, an email-OTP popup with SMTP delivery, a write-once email lock, and per-merchant playbook YAMLs. We removed all of it because:

- **Identity** — Chrome being unlocked + the user being at the keyboard is sufficient evidence the user is present. We don't need a second OTP channel.
- **Authorization** — saved cards in Chrome / 1Password / Apple Pay = previously approved. The user's "yes" in chat right before the click = approved now.
- **Fraud detection** — Visa, Mastercard, Stripe Radar, the issuing bank are all already doing this for every transaction. Adding our own audit didn't add anything they don't catch better.
- **Brittle selectors** — every merchant UI change broke a playbook. With Claude-in-Chrome MCP's `find` and `screenshot`, the agent reads the page adaptively.

The result: less code, less setup, fewer failure modes, broader merchant coverage (any site the user has used before, not just ones with a YAML).

What we lose: the cross-model-family audit. Acceptable for everyday top-ups under $100 against merchants with their own fraud detection.

## CLI reference

| Command | What it does |
|---|---|
| `broker log --merchant M --amount N [--rationale TEXT] [--status STATUS]` | Record an attempt; returns intent_id |
| `broker update <intent_id> --status STATUS [--note TEXT]` | Update an attempt |
| `broker history [--limit N] [--format pretty\|json]` | Recent attempts |
| `broker budget [--daily N] [--monthly N]` | Get / set caps |
| `broker check-budget <amount>` | exit 0 if amount fits; non-zero if it'd exceed caps |
| `broker export out.json` | Full ledger dump |

The CLI never drives a browser, sends an email, or contacts an external API. It only manages a SQLite ledger at `~/.claude/skills/kya-broker.local/ledger.sqlite`.

## Configuration

Set spending caps once via the CLI:

```bash
broker budget --daily 50 --monthly 500
```

Both are optional. Without them, no cap is enforced.

## Migration from v0.5

If you used v0.5:

```bash
# Your ledger and config are in ~/.claude/skills/kya-broker.local/
# v1.0 uses a new ledger schema; the v0.5 SQLite file is preserved untouched
# but v1.0's `broker` commands won't see v0.5 intents.

# To start fresh under v1.0:
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"

# v1.0 ledger lives at .local/ledger.sqlite (new schema, separate from v0.5's audit_results / human_gates / etc.)
```

The email lock, SMTP creds, and playbooks from v0.5 are no longer used and can be ignored or deleted from `kya-broker.local/`. The `.env` file is no longer needed.

See [docs/migration.md](docs/migration.md) for details.

## Development

```bash
git clone https://github.com/ssssydney/kya-broker
cd kya-broker
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/
```

## License

MIT. See [LICENSE](LICENSE).
