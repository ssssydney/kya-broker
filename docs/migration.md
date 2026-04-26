# Migrating from v0.5 to v1.0

v1.0 is **not backward-compatible** with v0.5 in CLI surface or stored data. Here's what to do.

## TL;DR

- **Reinstall** to overwrite the old broker binary:
  ```bash
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"
  ```
- **Replace your SKILL.md** with the v1.0 one:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/SKILL.md \
    -o ~/.claude/skills/kya-broker/SKILL.md
  ```
- **(Optional)** Wipe the v0.5 local state:
  ```bash
  # Backup first if you want history
  cp -r ~/.claude/skills/kya-broker.local ~/.claude/skills/kya-broker.local.v0.5-backup

  # Remove v0.5-specific files; keep the directory
  rm -f ~/.claude/skills/kya-broker.local/email_lock.json
  rm -f ~/.claude/skills/kya-broker.local/email_lock.salt
  rm -rf ~/.claude/skills/kya-broker.local/dumps
  ```
- v0.5's ledger.sqlite has more tables than v1.0 needs. v1.0 will create the new `intents` and `budget` tables alongside the old ones — no harm, but the old tables are unused going forward.

## What's gone in v1.0

| v0.5 thing | v1.0 status |
|---|---|
| `src/auditor/*` (Codex + Claude auditor) | Deleted |
| `src/popup_server.py` (local HTTP popup) | Deleted |
| `src/email_lock.py` (write-once email lock) | Deleted |
| `src/email_verifier.py` (SMTP OTP) | Deleted |
| `src/chrome_bridge.py` (CDP driver) | Deleted — agent uses Claude-in-Chrome MCP instead |
| `src/human_gate.py` (gate primitive) | Deleted |
| `src/intent.py`, `broker.py`, `setup_wizard.py`, etc. | Deleted |
| `playbooks/*.yaml` | Deleted |
| `prompts/*.md` (auditor + chrome agent prompts) | Deleted |
| `policy.default.yaml` | Deleted |
| `.env` SMTP config (`KYA_BROKER_SMTP_*`) | Ignored |
| `setup.py` 8-step wizard | Deleted; just install + maybe `broker budget` |
| `broker propose-intent` | Replaced by `broker log` |
| `broker resume`, `broker analyze-audits`, `broker email-lock` | Deleted (no audit / OTP / lock) |
| `kya-broker-mcp` MCP server | Deleted (not needed; agent uses Claude-in-Chrome MCP directly) |

## What's new / changed

| v1.0 thing | Notes |
|---|---|
| `broker log --merchant M --amount N` | Records an attempt; returns intent_id |
| `broker update <intent_id> --status STATUS` | Moves an intent to settled / failed / declined |
| `broker history` | Lists recent intents |
| `broker budget [--daily N] [--monthly N]` | Get / set caps |
| `broker check-budget <amount>` | Returns 0 if amount fits, non-zero otherwise — designed for shell-style guards |
| `broker export FILE` | Dump full ledger as JSON |
| Schema v1 ledger | Adds `intents` and `budget` tables |
| SKILL.md | Rewritten as a Claude-in-Chrome MCP playbook |

## Why the rewrite

The v0.5 architecture (audit + OTP + popup + playbooks) was reinventing infrastructure that browsers and payment processors already provide. The cost / benefit didn't pencil:

- Codex auditor caught ~0% additional fraud beyond what the user reading the chat would catch.
- Email OTP duplicated factor 1 (Chrome unlocked) and factor 3 (3DS).
- Playbook YAMLs broke every time a merchant changed their UI.
- 3500 lines of Python required setup wizard, SMTP config, Codex CLI install — tons of friction for a small benefit.

v1.0 trusts:
- Chrome being unlocked + signed in = user is present
- Chrome's saved cards / autofill = previously approved
- Stripe / Visa / bank = fraud detection
- User's "yes" in chat right before the click = present approval

That covers the same threat model with ~300 lines and zero setup wizard.

## If you want the old behaviour

Stay on v0.5 by checking out the tag:

```bash
git clone https://github.com/ssssydney/kya-broker
cd kya-broker
git checkout v0.5
bash install.sh
```

v0.5 is preserved at the `v0.5` tag for reference and reproducibility, but there are no plans to backport bug fixes to it. The recommended path is v1.0.
