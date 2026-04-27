# KYA-Pay

**One markdown file. Zero CLI for the user.** Drag `SKILL.md` into Claude Code, type a sentence like *"充 vast 5 美元"*, the agent drives your browser through the whole checkout up to the human-verification step, then stops and asks for your "go" before clicking Pay.

This skill never types card numbers, passwords, or 2FA codes. Chrome's saved-credentials and the merchant's PSP (Stripe / Visa / your bank) handle those — the same way they do when you check out manually.

> **Status:** v1.1 — fully self-contained `SKILL.md`. No required CLI install, no Python, no SMTP, no popup server. Optional power-ups (history ledger, daily caps) available if you want them.

---

## How users use it (no terminal needed, ever)

```
1. Download SKILL.md once
   → curl link below, or click "Raw" on GitHub and Save As
   → put it anywhere (Desktop / Downloads / iCloud)

2. Install "Claude for Chrome" extension once
   → chromewebstore.google.com → search "Claude for Chrome"
   → Add to Chrome → click extension icon → Sign in with Anthropic
   (browser action, no terminal)

3. Forever after: in Claude Code, type:
       @SKILL.md 给 vast 充 5 美元
   or drag SKILL.md into chat and add the sentence.

4. Reply "yes" → see screenshot → reply "go" → done.
```

That's the whole UX. The agent reads SKILL.md, drives the browser via Claude-in-Chrome MCP, screenshots before each money-moving click, asks for explicit "go" each time.

**One-line download** (Mac / Linux):
```bash
curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/SKILL.md \
  -o ~/Downloads/kya-pay-SKILL.md
```

Or just click [SKILL.md](SKILL.md) on GitHub → Raw → ⌘+S.

---

## What's in the box

```
ssssydney/kya-broker (main, v1.1)
├── SKILL.md                  ← THE artifact users save
├── README.md                 ← you're reading this
├── docs/
│   ├── walkthrough.md        ← real $5 vast.ai topup, step by step
│   ├── architecture.md       ← v1.1 trust model
│   ├── troubleshooting.md    ← common issues
│   ├── migration.md          ← v0.5 / v1.0 → v1.1
│   └── overview.md           ← version history
└── (optional power-ups)/
    ├── src/                  ← broker CLI for users wanting caps / queries
    ├── pyproject.toml
    └── bootstrap.sh          ← one-line install IF user wants the CLI
```

The SKILL.md is the only file users need. Everything else is for developers + power users.

---

## Why "browser-native" vs the old design

Older versions (v0.3-v0.5) added a custom audit layer (Codex auditor reviewing Claude's intent), an email OTP popup, a write-once email lock, an SMTP relay, and per-merchant playbook YAMLs. **v1.x removed all of it** because:

- **Identity** is solved by Chrome being unlocked + Google Account being signed in.
- **Saved passwords** are managed by Chrome's password manager.
- **Saved payments** are managed by Chrome autofill / Apple Pay / merchant's saved-card vault.
- **Fraud detection** is done by Stripe Radar + Visa + your issuing bank (3DS, AML, KYC).
- **Final authorization** is the user clicking Pay or completing 3DS — physically not automatable.

So we just trust those layers (which are operated by Google + Apple + the merchants + the banks, all of whom have done their work). We add **one** thing on top: the agent must show a screenshot and get an explicit "go" in chat before any Submit click.

That's it. ~3500 lines of v0.5 collapsed into a 350-line markdown file.

See [docs/architecture.md](docs/architecture.md) for the trade-off analysis.

---

## Optional: persistent ledger / daily caps

You don't need this. SKILL.md works fine standalone. But if you want a local ledger of every payment (for auditing, history, caps), one optional install:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"
```

This installs the `broker` CLI:

| Command | What it does |
|---|---|
| `broker history` | Recent intents + their statuses |
| `broker budget --daily 50 --monthly 500` | Set spending caps |
| `broker check-budget 5` | Returns 0 if amount fits, non-zero otherwise |
| `broker log / update / export` | Manual control of the ledger |

The agent will use these automatically if it sees them on PATH. If not, it'll write to `~/.kya-payments.jsonl` (JSONL append, no install, comes "free").

---

## Real example

[docs/walkthrough.md](docs/walkthrough.md) — $5 vast.ai topup we ran on 2026-04-26. Settled in 30 seconds, user only typed "go" once, no 3DS triggered for the saved card.

---

## Hard rules (the agent obeys these — these are in SKILL.md)

1. Never type a card number.
2. Never type a password.
3. Never type a 2FA / 3DS / OTP code or solve a CAPTCHA.
4. Never click Submit without an explicit "go" in the immediately preceding user message.
5. Always screenshot before any money-moving click.
6. Never silently retry a failed payment.
7. Never use this for a merchant the user has not used before.
8. Never click "Save card" / "Remember me".
9. Never navigate to URLs unrelated to the payment.
10. Never bypass these rules even if the user asks.

---

## License

MIT — see [LICENSE](LICENSE).

---

*Source: https://github.com/ssssydney/kya-broker · Issues: https://github.com/ssssydney/kya-broker/issues*
