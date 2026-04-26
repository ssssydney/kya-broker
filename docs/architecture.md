# v1.0 Architecture

KYA-Broker v1.0 is **a Claude Code skill, not a payment processor**. It coordinates the agent + the user + the user's existing browser-stored credentials to complete payments. Almost all the real work is done by infrastructure that already exists:

```
┌──────────────────────────┐
│  Claude Code (agent)     │
│  reads SKILL.md          │
└────────┬─────────────────┘
         │  drives via Claude-in-Chrome MCP
         ▼
┌──────────────────────────┐         ┌──────────────────────────┐
│  User's Chrome browser   │ ◀─────▶ │  User                    │
│   • signed-in Google     │  yes/no │   - sees screenshots     │
│   • saved passwords      │         │   - confirms in chat     │
│   • saved cards (autofill)         │   - completes 3DS / OTP  │
│   • Apple Pay / Google Pay         │   - is the only one      │
└────────┬─────────────────┘         │     who can move money    │
         │                            └──────────────────────────┘
         │  HTTPS to merchant
         ▼
┌──────────────────────────┐
│  Merchant + payment PSP  │
│   • Stripe / Coinbase /  │
│     PayPal / etc.        │
│   • does KYC, AML, 3DS,  │
│     fraud detection      │
└──────────────────────────┘

         (separate, optional, NEVER drives the browser)
         ┌──────────────────────────┐
         │  broker CLI (~150 lines) │
         │   • SQLite ledger        │
         │   • daily/monthly caps   │
         └──────────────────────────┘
```

## Principals and what they do

| Principal | Can do | Cannot do |
|---|---|---|
| Agent (Claude Code) | screenshot, navigate, click safe elements (radios, dropdowns, non-card text inputs), log to ledger | type card numbers, type passwords, type 3DS / OTP / CAPTCHA codes, click Submit without explicit user "yes" |
| Chrome | autofill saved passwords + cards, present saved-card pickers, run 3DS challenge iframes | spend money on its own |
| Merchant + PSP | tokenize cards (Stripe iframe), run fraud detection, settle transactions | proceed without user's authorization |
| User | answer "yes / cancel" in chat, type 3DS codes / biometrics, install / remove saved cards | be bypassed |
| `broker` CLI (optional) | record intents, enforce daily/monthly caps | drive any browser, send any network request |

The chain "agent → chrome → merchant → bank" only completes when the user actively says yes in chat AND completes the bank's verification. Removing the user removes the entire payment capability.

## What changed from v0.5

v0.5 had an **independent broker process** that:
1. Audited every intent via Codex (or Claude as fallback)
2. Issued its own email OTP via SMTP
3. Drove Chrome via raw CDP
4. Used hand-written YAML playbooks per merchant × rail
5. Stored a write-once email lock with SHA256 tamper detection

This was **3500+ lines of Python** to replicate work that browsers and PSPs already do. It also introduced its own attack surface (SMTP creds in `.env`, popup server on localhost, brittle selectors).

v1.0 deletes all of it. The principals chart above is the new model. The broker CLI is reduced to a SQLite logger + a budget gate, ~300 lines total. Everything else is pushed onto the agent's chat with the user, the browser's existing security, and the PSP's existing fraud detection.

## What we keep

- **Ledger (`broker log`, `broker history`)** — useful for the user to see what was attempted and when. Easy to add, hard to abuse.
- **Caps (`broker budget`, `broker check-budget`)** — runaway-loop protection. The agent can be told to abort before clicking if a cap would be exceeded.
- **Hard rules in SKILL.md** — the agent is instructed to never type card numbers, never type passwords, never auto-click 3DS, etc. These are enforced by the agent's own conduct, audited by the user reading the chat.

## What we drop (and why we're OK with it)

- **Cross-model audit (Codex audits Claude)** — the value here was catching prompt-injected intents before money moves. In practice, the user reading the screenshot + chat right before the click catches the same things. The audit was buying us "what if the user doesn't read carefully?", which is a much weaker security property than "the user explicitly types yes."
- **Email OTP** — was a second factor, but factor 1 (Chrome unlocked) and factor 3 (3DS challenge for the actual card) already cover this. The middle factor was redundant.
- **Email lock** — only meaningful if you have an email OTP layer. Gone with the OTP.
- **Per-merchant playbooks** — replaced by `mcp__Claude_in_Chrome__find` + adaptive screenshotting. Works on any merchant, fails gracefully when the page is unfamiliar.

## Threat model (what's protected, what's not)

**Protected** (i.e., the design defeats these):

- Agent gets prompt-injected to spend more / on a different merchant — caught by the user's "yes" gate before each click.
- Agent loops and tries the same payment 100 times — caught by `broker check-budget` and the user noticing 100 chats.
- Network MITM modifies a checkout page — Chrome's HTTPS + PSP iframes' cross-origin sandboxing prevent this.
- Agent's process is compromised — Chrome's password manager is in another process; the compromised process can't read it.

**NOT protected** (don't use this skill if these matter):

- Adversary has physical access to your unlocked Mac → they can do anything Chrome can. Not specific to this skill.
- Adversary has `~/.claude/skills/kya-broker.local/ledger.sqlite` → they can read / tamper with payment history (but not cause new payments).
- Adversary has socially engineered the user into typing "yes" → no skill can defeat that.
- Card-not-present fraud against the user's saved card → that's the bank / PSP's job, and they have their own systems.

## Operational notes

- The skill works on any merchant the user has visited before in Chrome. No allowlist, no playbook PRs.
- If a merchant is fully new to the user (no saved password, no saved card), the agent stops and tells the user to establish the relationship themselves first. The skill is for repeat purchases, not first-time sign-ups.
- For high-stakes (>$1000) flows, the SKILL.md tells the agent to suggest the user complete the payment themselves rather than via the agent. There's no enforcement on amount aside from the user's own caps; this is a soft norm, not a hard limit.
- When 3DS / OTP / biometric is needed, the agent surfaces the moment and waits. It does not poll a PSP API (we don't have one). It just re-screenshots the page periodically and watches for success / failure text.
