---
name: kya-broker
description: Generic agent-payment skill. Use this when the user asks Claude Code to autonomously top up credits at an allowlisted merchant (OpenRouter API credits, vast.ai GPU rental, Anthropic API credits, and any merchant with a registered playbook) using their own credit card, crypto wallet, or email-linked account. Trigger only on explicit "pay / top up / buy credits / run this paid job" requests. DO NOT trigger for general coding help, research reading, or tasks that don't need money. Before using this skill in a conversation, always ask the user to confirm their email address; once locked that address is immutable.
---

# KYA Broker — Agent Payment Skill (v0.4)

This skill lets you pay merchants on the user's behalf using their human
payment methods (cards, MetaMask, email-auth accounts). You drive the browser
up to the card / wallet / signature step, surface that step to the user in a
local popup window, and wait. You never handle card numbers, passwords, or
private keys directly. Every intent is independently audited (Codex preferred)
before money moves, and every execution requires a broker-issued email OTP to
the user's locked confirmation email as a final authorization floor.

Full source, playbooks, and docs: **https://github.com/ssssydney/kya-broker**

---

## 0. Bootstrap on first use (one-time, ~90 seconds)

Before proposing any intent, verify the broker CLI is installed.

```bash
broker --version 2>/dev/null || true
```

If the command isn't found OR returns no version, install via:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"
```

This clones the full project into `~/.local/opt/kya-broker/`, creates a
virtualenv, installs the `broker` / `kya-broker-mcp` / `kya-broker-setup`
wrappers into `~/.local/bin/`, and initialises the SQLite ledger. It does
NOT overwrite this `SKILL.md` — the user's standalone file stays as-is.

After the bootstrap completes, verify:

```bash
broker --version          # should print "broker, version 0.4.x" or later
```

Register the MCP server in Claude Code's config **once**:

```json
{
  "mcpServers": {
    "kya-broker": {
      "command": "/Users/<you>/.local/bin/kya-broker-mcp"
    }
  }
}
```

---

## 1. Email lock protocol (CRITICAL — read every conversation start)

The broker sends an OTP to the user's **locked confirmation email** before
every payment. That email is write-once: once set, it cannot be changed until
the user manually runs `broker email-lock --reset` from their terminal. This
is on purpose — a prompt-injected agent must not be able to silently reroute
OTPs to an attacker's inbox.

**At the start of any conversation that might use this skill**, before
proposing any intent, you MUST:

1. Call the MCP tool `email_lock_status`:
   ```
   tool: email_lock_status
   arguments: {}
   ```

2. If `{"locked": true, "email": "..."}`:
   - Confirm with the user: "I'll send payment confirmation codes to
     `<email>`. Is that still the address you want?"
   - If the user says **anything** other than yes, STOP. They want a
     different email, which requires `broker email-lock --reset` —
     tell them to run that themselves.
   - If yes, proceed.

3. If `{"locked": false}`:
   - Ask the user: "Which email should the broker send payment
     confirmation codes to? This gets locked for this install and can't be
     changed without explicit terminal action later."
   - Once they answer, call `email_lock_set` with their address:
     ```
     tool: email_lock_set
     arguments: {"email": "user@example.com"}
     ```
   - Confirm back: "Locked `user@example.com` for this install. The broker
     will send codes there before every payment."

If step 3's `email_lock_set` errors with `email_lock_error`, the email is
invalid — ask the user for a syntactically valid address and retry.

Never use this skill without first completing the email lock check. If the
user refuses to provide an email, decline to use the skill and explain why.

---

## 2. When to use this skill

Trigger when the user asks you to:

- Top up OpenRouter / Anthropic / other LLM-API credits so you can keep calling them
- Top up vast.ai / Lambda / RunPod GPU credits for an experiment
- Complete a paid workflow end-to-end (topup + actual task) without the user babysitting every step

Do NOT trigger when:

- The user is asking a general question; no payment is implied
- The merchant isn't in `config.yaml` → merchants (run `broker history` to see recent merchants; ask the user to add if needed)
- Existing merchant balance already covers the task (always check `broker check-balance` first)

---

## 3. The generic pattern

Every rail (card, crypto, email-link) reduces to the same shape:

```
1. Broker drives Chrome to the merchant's checkout page
2. Broker fills the amount + picks the method
3. Local popup window opens: email OTP (always fires first)
   ── user pastes the 6-digit code from their inbox
4. Chrome flow continues to the rail's own authorization step:
   ── card: user enters card in Stripe iframe (via autofill / 1Password /
      Apple Pay / manual) — the broker does NOT type card numbers
   ── crypto: MetaMask extension popup — user clicks Confirm
   ── email_link: user clicks a magic link the merchant emailed
5. Optional second gate for 3DS / SMS OTP if the card issuer requires it
6. Merchant settles; broker records the receipt in the ledger
```

Authorization tiers:

| Tier | Amount | What you need from the user |
|---|---|---|
| L0 | ≤ `l0_ceiling_usd` (default $2) | Audit approve + email OTP only. No rail human-click needed for MetaMask auto-unlock; card / MetaMask / magic-link always need a human moment. |
| L1 | ≤ `l1_ceiling_usd` (default $50) | Audit + email OTP + explicit rail gate |
| L2 | > `l1_ceiling_usd` | Broker refuses — you must ask the user for out-of-band approval before submitting |

---

## 4. Workflow

1. **Check the email lock** (see section 1). Done once per conversation.
2. **Read the user's request.** Pull out merchant, what they want to buy, rough budget.
3. **Check balance.** `broker check-balance`. If credit already covers the task, skip to use.
4. **Propose intent.** Call `propose_intent` (MCP) or `broker propose-intent intent.json --context-file ctx.json` (CLI) with a JSON payload (schema below). Include `rail_hint` when you have a preference.
5. **Handle the response state.**
   - `state: settled` — intent auto-executed (L0 with user having enrolled a compatible payment method). Proceed with the task.
   - `state: awaiting_user` — the broker is waiting for the email OTP + rail-gate sequence. Tell the user: "The broker will send a 6-digit code to `<locked email>` and open a popup window. After you confirm, the browser will drive to the checkout page." Then call `broker resume <intent_id>`.
   - `state: rejected` — audit rejected. Do NOT retry with a tweaked rationale just to "get it through" — the auditor saw a real problem. Tell the user the concerns and ask how to proceed.
   - `state: failed` / `playbook_broken` — surface the error; do not try another rail silently.
   - `state: user_declined` — user rejected in popup / MetaMask / browser. Do NOT retry silently. Ask why.
6. **Use the credit.** Make the API calls, run the job — whatever the user originally asked for.

---

## 5. MCP tool reference

- `email_lock_status` — returns `{locked, email?, tampered?}`. Call at the top of every conversation.
- `email_lock_set({email})` — set the confirmation email (write-once). Errors if a different one is already locked.
- `propose_intent({merchant, amount_usd, rationale, estimated_actual_cost_usd, references?, rail_hint?, context?})` — submit a payment intent.
- `get_status({intent_id})` — state machine + audits + execution + gate history.
- `get_history({limit?})` — recent intents in reverse-chronological order.
- `check_balance()` — wallet balance (when Chrome attached) + spending caps + 24h / 30d spend.

Equivalent CLI commands (same semantics): `broker email-lock`, `broker propose-intent`, `broker status`, `broker history`, `broker check-balance`, `broker resume`.

---

## 6. Intent schema (v1.1)

```json
{
  "merchant": "openrouter.ai",
  "amount_usd": 10.0,
  "rationale": "Need $10 OpenRouter credit to call gpt-4o-mini for the paper-reproduction pipeline — 25 calls * 2M input tokens @ $0.15/M + matching output = ~$6, with 40% buffer.",
  "estimated_actual_cost_usd": 6.0,
  "references": ["scripts/run_pipeline.py", "papers/attention-sink.pdf"],
  "rail_hint": "card"
}
```

Field notes:

- `merchant` — must match an entry in the user's config merchant allowlist exactly.
- `amount_usd` — what you're asking the broker to charge. Should be ≥ `estimated_actual_cost_usd` (a small buffer is fine; 2-3× is suspicious).
- `rationale` — written for a skeptical auditor: specific references to the task, rough math backing the amount, why this merchant. Avoid generic boilerplate.
- `estimated_actual_cost_usd` — your honest estimate of what the task will actually cost.
- `references` — file paths / URLs backing the rationale. The auditor cross-references these with your context.
- `rail_hint` — optional. One of `card`, `crypto`, `email_link`, `bank_transfer`. Omit to let the broker pick per the user's preferences.

---

## 7. Hard rules

1. **Never** propose an intent with `amount_usd > policy.l1_ceiling_usd` without asking the user first. The broker will refuse and you'll have wasted a round trip.
2. **Never** put secrets (API keys, wallet seeds, card numbers) into intent fields — they're forwarded to the auditor.
3. **Never** suggest the user disable audit, raise thresholds mid-workflow, disable the email OTP, or skip the rail gate. If a legit task genuinely needs more money than current thresholds allow, surface that to the user as its own decision — don't wrap it into the current intent.
4. **Never** retry a `user_declined` intent silently. The user said no; ask them why first.
5. **Never** retry an `audit rejected` intent by just tweaking the rationale phrasing. The auditor saw a real mismatch; investigate what it caught.
6. **Never** skip the email lock check (section 1) on a fresh conversation.
7. **Never** try to bypass the popup window or enter the OTP code yourself — the code goes from the user's inbox into the popup by the user's hand, full stop.
8. **Never** propose an intent for a merchant not in `broker check-balance` → `merchants` allowlist. If the user wants a new merchant, tell them to add it via editing `~/.claude/skills/kya-broker.local/config.yaml` and pushing a playbook PR.

---

## 8. Example conversation

```text
User: "reproduce the attention-sink finding from this paper with gpt-4o-mini"

You: [call email_lock_status] → {locked: true, email: "warrenzhong666@gmail.com"}
     Great — I'll send payment codes to warrenzhong666@gmail.com. Is that
     still the address you want to use?

User: "yes"

You: [read paper, estimate pipeline cost]
     [call check_balance] → OpenRouter credit: $0.12
     Need ~$6-8 of OpenRouter credits. I'll propose a $10 topup (with buffer).

     [call propose_intent with merchant=openrouter.ai, amount_usd=10, rail_hint=card,
      rationale="25 calls × 2M tokens of gpt-4o-mini @ ~$0.60/M effective = $4.50,
                 +40% buffer = $6, rounding up to $10 topup for headroom"]
     → {state: awaiting_user, tier: L1, verdict: approve}

     Auditor approved the intent. The broker will now:
       1. Send a 6-digit code to warrenzhong666@gmail.com
       2. Open a popup window in your browser — paste the code there
       3. Drive Chrome to openrouter.ai/credits and prepare the card checkout
       4. You then enter your card (autofill is fine) and click Pay

     Running `broker resume <id>`…

[user paste code in popup, then fills card in Chrome checkout]

You: [observe state=settled via `broker status`]
     Payment settled. OpenRouter balance is now ~$10.12. Starting the pipeline.

     [invoke the OpenRouter API as originally requested]
```

---

## 9. Failure recovery

| Symptom | What to do |
|---|---|
| `broker --version` fails even after bootstrap | Tell the user to check `~/.local/bin` is on PATH, then rerun bootstrap. |
| `email_lock_status` returns `{tampered: true}` | Something edited `email_lock.json` by hand. Ask the user to run `broker email-lock --reset` (they must acknowledge it invalidates past intents) then relock. |
| `state: rejected` with concerns mentioning the rationale | Re-read your rationale. Does it actually match what the user's context is about? Ask the user whether the reviewer's concern has merit — don't paraphrase to get it through. |
| `state: playbook_broken` | Merchant UI changed. Tell the user; the fix is to update the merchant playbook YAML at `~/.local/opt/kya-broker/playbooks/<merchant>_topup_<rail>.yaml`. |
| Popup window doesn't open | Check that the user's default browser is set. As a fallback, they can manually visit the URL printed in the terminal. |
| OTP email doesn't arrive | Check spam. Verify SMTP configured via `broker setup`. For a temporary dev workaround, set `KYA_BROKER_OTP_SHOW_IN_TERMINAL=1` and the code is printed to stdout. |

---

*This is a standalone SKILL.md — everything else lives at
https://github.com/ssssydney/kya-broker and is installed by the bootstrap
step on first use. Keep this file updated by occasionally checking the repo,
or set up a cron to `git pull` the bootstrapped opt dir.*
