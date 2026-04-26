---
name: kya-broker
description: Browser-native agent payment skill. Use when the user asks Claude Code to make a paid action on the web (top up credits at any merchant, pay a subscription, complete a checkout, etc.) AND the user has previously interacted with that merchant in their Chrome browser (saved password, autofilled card, or saved checkout method). Trigger on explicit "pay / top up / buy / charge / 充值 / 付款 / 支付" requests. Do NOT trigger for general coding help, research reading, or any task that doesn't involve money. Before any money-moving click, you must show the user a screenshot of what's about to happen and get an explicit "yes" in chat.
---

# KYA Broker — Browser-Native Payment Skill (v1.0)

You drive the user's Chrome via the **Claude-in-Chrome MCP** to complete payment workflows on any merchant where the user has an existing account and saved payment methods. You do NOT touch card numbers, passwords, or seed phrases — Chrome's own password manager and saved-payments features handle those, and the merchant's checkout (Stripe / PayPal / etc.) handles the actual transaction. Your job is to drive the browser up to the human-verification moment, surface that moment to the user, and stop.

Source / updates: **https://github.com/ssssydney/kya-broker**

---

## 0. Philosophy (read this first)

Earlier versions of this skill (v0.3-v0.5) added a custom audit layer, an email OTP popup, a write-once email lock, an SMTP relay, and per-merchant playbook YAMLs. **All of that has been removed.** It was reinventing infrastructure that browsers and payment companies already do better:

- **Identity** is solved by Chrome being unlocked + Google Account being signed in. If the user is at their unlocked Mac with Chrome open, they ARE the user.
- **Saved passwords** for site logins are managed by Chrome's password manager (or 1Password, Bitwarden — whatever the user uses).
- **Saved payment methods** are managed by Chrome autofill, Apple Pay, Google Pay, and the merchant's own card-on-file storage. They've done the KYC.
- **Fraud detection** is done by the issuing bank (3DS), the network (Visa/MC), and the merchant (Stripe Radar).
- **Final authorization** is the user clicking Pay or completing 3DS, which the broker physically cannot do.

The only thing this skill adds on top of "Claude drives a browser" is:

1. **Friction at money-moving moments** — you must screenshot and ask the user "yes" in chat before clicking Submit / Pay / Confirm.
2. **A local ledger** (optional, via the `agentpay` CLI) — so the user can see history and enforce daily/monthly caps.
3. **A clear set of hard rules** for what you do and don't touch in the browser.

That's it. Keep it minimal.

---

## 1. Bootstrap (one-time, ~30 seconds)

The `agentpay` CLI is optional but useful for spending caps + history. To install:

```bash
broker --version 2>/dev/null || \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"
```

After install, `broker` is available on PATH. If you can't or don't want to install, you can still use this skill — just skip steps that mention `broker`.

You also need the **Claude for Chrome** browser extension installed and signed in for `mcp__Claude_in_Chrome__*` tools to work. If `mcp__Claude_in_Chrome__list_connected_browsers` returns `[]`, ask the user to install it from `https://chromewebstore.google.com/` (search "Claude for Chrome") and sign in.

---

## 2. When to use this skill

Trigger when the user asks for:

- "Top up X dollars / credits at Y merchant" (vast.ai, OpenRouter, Anthropic, Replicate, …)
- "Pay for / renew this subscription"
- "Buy / order this thing on Amazon / Taobao / etc."
- Any web checkout where the user **already has an account** and **already has a payment method on file** in Chrome or with that merchant.

Do NOT trigger when:

- The user is asking a question or doing research
- The merchant is unfamiliar (newly registered domain, no Chrome saved credentials, user hasn't used it before)
- The user explicitly says not to make payments
- The flow requires **entering NEW card details for the first time** (the user should do that themselves; you don't type card numbers, ever)

---

## 3. The workflow

### Step 1 — Intent confirmation (in chat, before any browser action)

Confirm with the user **in chat** before opening the browser:

> "I'm going to: (a) navigate to **<merchant>**, (b) sign you in if needed, (c) start a top-up of **\$<amount>** using your saved payment method. Confirm?"

Do not start the browser flow until they say yes (or equivalent). If the user has set spending caps via `broker budget`, run `broker check-budget <amount>` first; if it fails, surface the cap to the user before asking for confirmation.

### Step 2 — Drive the browser

Use these MCP tools:

- `mcp__Claude_in_Chrome__list_connected_browsers` — confirm Chrome is connected
- `mcp__Claude_in_Chrome__select_browser` — pick the user's Chrome (use `isLocal: true`)
- `mcp__Claude_in_Chrome__tabs_context_mcp` — get the MCP tab group
- `mcp__Claude_in_Chrome__tabs_create_mcp` — make a fresh tab for this conversation
- `mcp__Claude_in_Chrome__navigate` — go to the merchant's page
- `mcp__Claude_in_Chrome__read_page` — inspect interactive elements (refs)
- `mcp__Claude_in_Chrome__find` — locate elements by natural language
- `mcp__Claude_in_Chrome__computer` — `screenshot`, `left_click`, `type`, `scroll` etc.

Always create a new tab for the payment flow — don't reuse the user's existing tabs.

### Step 3 — Login (if needed)

If you land on a sign-in page:

- **Google OAuth button visible?** Click it — Chrome will use the signed-in Google account automatically.
- **Email + Password fields?** Click into the email field; if Chrome's password manager has saved credentials, an autofill suggestion appears — click it. The fields populate automatically. Click sign in.
- **Neither autofills nor has a Google option?** STOP. Ask the user to sign in manually in the tab, then say "ok ready" before continuing.
- **2FA / OTP screen?** STOP. Ask the user to complete it in the tab, then say "ready".

You never type a password directly. You never type a 2FA code.

### Step 4 — Fill the checkout form

Once on the checkout / top-up page:

- **Amount field** — type the amount the user authorized.
- **Payment method radio** — click the saved card / saved payment method (e.g., "VISA…3497"). **Never click "Add new card" or any flow that requires typing a card number.** If no saved method is visible, STOP and tell the user to add one manually first.
- **Avoid clicking Submit / Pay / Confirm yet.**

### Step 5 — Screenshot + final confirm in chat

Take a screenshot of the form in its final state with `screenshot` action. Show the user:

> "About to charge **\$<amount>** to **<saved method>** on **<merchant>**. <screenshot>. Confirm with 'go' to proceed, or 'cancel'."

Do not click Submit until they reply with an explicit "go" / "yes" / "confirm" / "proceed" or equivalent. **Anything ambiguous = cancel.**

If the user has the `broker` CLI installed, log the attempt before clicking:

```bash
broker log --merchant <m> --amount <a> --rationale "<short reason>" --status proposed
```

This returns an `intent_id` you'll use to update status later.

### Step 6 — Submit

Click the Pay / Submit / Confirm button. Take a screenshot immediately after.

### Step 7 — Handle verification gates

The merchant's payment processor may now show:

- **3D-Secure (3DS) challenge** — bank's own page asking for SMS code, app push, or biometric. This is in an iframe or popup. **Tell the user** "your bank is asking for verification — complete it in the tab", then poll for completion via repeated screenshots.
- **OTP / SMS code** — same pattern.
- **CAPTCHA** — STOP. The user must do this themselves. Do not try to bypass.

You never type a 3DS code, an OTP, or solve a CAPTCHA. If the verification page sits there for 3+ minutes with no progress, surface it to the user as a probable failure.

### Step 8 — Verify settlement

Once the page shows success ("Payment received", "Credits added", "Thank you", balance updated):

- Take a final screenshot
- If `broker` is installed: `broker update <intent_id> --status settled --note "<receipt id if visible>"`
- Tell the user: "Settled. \$<amount> charged. New balance / credit visible: <X>."

If instead you see "Payment failed", "Card declined", or the page hangs:

- Take a screenshot
- `broker update <intent_id> --status failed --note "<error text>"`
- Tell the user the literal error and stop. Do not retry without their explicit approval.

---

## 4. Hard rules

1. **Never type a card number.** Use Chrome's saved cards / autofill only. If checkout requires entering a new card from scratch, STOP and tell the user.
2. **Never type a password.** Use Chrome's password manager autofill only. If autofill doesn't trigger, ask the user to sign in.
3. **Never type a 2FA code, OTP, 3DS code, or CAPTCHA.** Those are the user's exclusively.
4. **Never click Submit / Pay / Confirm without an explicit "yes" / "go" in chat right before.** Auditor approvals from earlier in the conversation do not count.
5. **Always screenshot before any money-moving click.** The user must see the actual page state.
6. **Never retry a declined or failed payment silently.** Always ask the user first.
7. **Never use a payment flow on a merchant the user has not used before.** No saved card = no go. The user must establish the relationship themselves first.
8. **If `broker check-budget <amount>` says the cap would be exceeded, abort and tell the user.** Do not attempt to bypass the cap.
9. **Never click "Save card", "Remember me", or similar persistence toggles** — that's the user's choice.
10. **If unsure, stop and ask.** The cost of a delayed payment is much lower than a wrong payment.

---

## 5. Optional CLI commands

If `broker` is on PATH:

| Command | What it does |
|---|---|
| `broker log --merchant M --amount N [--rationale TEXT]` | Log a proposed intent. Returns intent_id. |
| `broker update <intent_id> --status settled\|failed\|declined [--note TEXT]` | Update an existing intent. |
| `broker history [--limit N] [--format pretty\|json]` | Recent intents. |
| `broker budget [--daily N] [--monthly N]` | Get / set caps. |
| `broker check-budget <amount>` | Returns 0 if amount fits within remaining cap, non-zero with message if it doesn't. |

The CLI uses a SQLite ledger at `~/.claude/skills/kya-broker.local/ledger.sqlite`. It does NOT drive the browser, send emails, or do anything beyond track + cap.

---

## 6. Example session

```
User: "充 5 美元到 vast"

You: I'll: navigate to cloud.vast.ai/billing, open Add credits, select Other → 5,
     keep VISA…3497 (saved Primary card), screenshot, then ask you to confirm.
     Daily budget check ok? (broker check-budget 5)

User: "go"

You: [navigate, click Add Credit, click Other, type 5, screenshot]
     <screenshot>
     About to charge $5 to VISA…3497 on vast.ai. Confirm with "go" to click
     "Add credit", or "cancel" to abort.

User: "go"

You: [click Add credit] [screenshot] 
     [3DS popup appeared] Your bank is asking for verification — complete it
     in the tab. I'll wait.

User: [completes 3DS]

You: [screenshot shows balance updated]
     Settled. $5 charged to VISA…3497, vast credit now $4.39.
     broker logged as intent abc-12345 (settled).
```

---

## 7. What you give up vs v0.5

| v0.5 | v1.0 |
|---|---|
| Codex auditor (cross-model-family review) | Just the user's "yes" in chat |
| Email OTP via SMTP | Just the user's "yes" in chat |
| Write-once email lock | n/a |
| Per-merchant playbook YAML | Generic Claude-in-Chrome MCP |
| ~3500 lines of Python | ~300 lines of Python |
| Setup wizard with 8 steps | `bootstrap.sh` install + done |
| Brittle hardcoded selectors | Adaptive `find` + screenshot |

The **only** safety property we lose is the cross-model-family audit. The user is opting to trust:
- Their Chrome being unlocked = they're present
- Their saved cards = they previously approved this card
- Stripe / Visa / their bank for fraud detection
- Their own "yes" in chat before each click

That's a totally reasonable trust model for everyday merchant top-ups under $100. For high-stakes flows (transferring large sums, novel merchants, sensitive enterprise data), you should still surface concerns and ask the user to do it themselves.

---

*Source + bootstrap: https://github.com/ssssydney/kya-broker*
