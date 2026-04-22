---
name: kya-broker
description: Generic agent-payment skill. Triggers when the user wants Claude Code to autonomously pay a merchant (OpenRouter API credits, vast.ai GPU rental, Anthropic credits, and similar) using the user's own credit card, crypto wallet, or email-authorised account. Trigger only when the user explicitly asks to top up / buy credits / run a paid job on an allowlisted merchant. Do NOT trigger for general coding questions, local experiments, or unrelated spending requests.
---

# KYA Broker — Agent Payment Skill (v0.4)

A generic broker that lets the agent pay any allowlisted merchant using the
user's human payment methods. You propose an intent; an independent auditor
reviews it; the broker drives Chrome to the merchant's checkout; when the flow
hits a moment only a human can complete (card entry, 3D-Secure, OTP, magic-link
click, MetaMask signature), the broker surfaces that moment clearly and waits.
You never touch card numbers, passwords, or private keys.

## When to use this skill

Use it when the user asks you to:

- Top up OpenRouter / Anthropic / other LLM-API credits so you can keep using them
- Top up vast.ai / Lambda / RunPod GPU credits for an experiment
- Run an end-to-end paid workflow (topup + use) without the user babysitting every step

Do NOT use it when:

- The user is just asking a question; no payment is implied
- The merchant is not in `config.yaml` → merchants
- Existing balance is enough for the task (check `broker check-balance` first)

## The generic pattern

Every rail (card, crypto, email_link) reduces to the same shape:

1. Broker drives Chrome to the merchant's checkout page
2. Broker fills the amount + picks the method
3. **Human gate** fires — broker surfaces a clear prompt ("enter card in the tab",
   "sign in MetaMask", "check your email"), waits up to N seconds
4. User completes the step in the browser (or their email / phone / wallet)
5. Broker detects completion via page text / URL / selector signals
6. Merchant settles; broker records the receipt

Authorization tiers map to this:

| Tier | Amount | Who approves |
|---|---|---|
| L0 | ≤ `l0_ceiling_usd` | Audit approves → auto-proceed. Card/crypto rails still require a human gate for card/wallet interaction. |
| L1 | ≤ `l1_ceiling_usd` | Audit approves + explicit human gate |
| L2 | > `l1_ceiling_usd` | Broker refuses. Ask the user before submitting. |

## Workflow

1. **Read the user's request.** Pull out: the merchant, what they want to buy,
   rough budget.
2. **Check balance.** `broker check-balance`. If merchant credit already covers
   the task, skip to use.
3. **Propose intent.** Call `broker propose-intent` with a JSON payload (schema
   below). Include `rail_hint` when you have a preference (e.g. card for OpenRouter).
4. **Watch state.** The broker may auto-execute (L0) or return `awaiting_user`
   (L1). In both cases a human-gate moment happens in the browser.
5. **Resume after human action.** For `awaiting_user` you call
   `broker resume <intent_id>` once the user says they've signed / entered
   card / clicked the magic link; for L0, the broker polls automatically.
6. **Use the credit.** Make the API calls, run the job — whatever the user
   originally asked for.

## CLI reference

- `broker propose-intent <intent.json> [--context-file <ctx.json>]` — submit intent
- `broker status <intent_id>` — state machine + audits + execution
- `broker check-balance` — merchant credit + spending caps (crypto wallet balance when Chrome is attached)
- `broker history [--limit N] [--format pretty|json]` — recent intents
- `broker resume <intent_id>` — continue past an awaiting_user gate
- `broker analyze-audits [--since YYYY-MM-DD]` — Codex vs Claude verdict A/B (shadow mode only)

MCP tool names map 1:1: `propose_intent`, `get_status`, `get_history`, `check_balance`.

## Intent schema (v1.1)

```json
{
  "merchant": "openrouter.ai",
  "amount_usd": 10.0,
  "rationale": "Need $10 of openrouter credits to call gpt-4o-mini for the paper-reproduction pipeline (~5 runs * 2M tokens @ $0.60/M = $6, with 40% buffer).",
  "estimated_actual_cost_usd": 6.0,
  "references": ["scripts/run_pipeline.py"],
  "rail_hint": "card"
}
```

`rail_hint` is optional; omit it to let the broker pick from config preferences.
Valid values: `card`, `crypto`, `email_link`, `bank_transfer`.

## Hard rules

- Never propose an intent with `amount_usd > policy.l1_ceiling_usd` without
  asking the user first.
- Never put secrets (API keys, wallet seeds, card numbers) anywhere in the
  intent — they are forwarded to the auditor.
- Never suggest the user disable audit, raise thresholds mid-workflow, or skip
  the human gate — if a legit task needs more money, surface that to the user
  as its own decision.
- If `broker status` returns `playbook_broken` or `failed`, stop and tell the
  user. Do not try to complete the payment manually through another path.
- Never retry a `user_declined` intent silently. Ask the user why, adjust, try again.

## Example: topping up OpenRouter credits

```text
User: "Run the paper-reproduction pipeline."

You: Need ~$6 of OpenRouter credits. `broker check-balance` → current credit $0.12.
    Propose intent: {merchant: openrouter.ai, amount_usd: 10, rail_hint: card,
                      rationale: "5 runs * 2M tokens @ $0.60/M + buffer"}
    Auditor approves. Broker drives Chrome to openrouter.ai/credits.
    Human gate fires: "Enter your card in the Stripe iframe."
    User autofills from 1Password, clicks Pay.
    Broker sees 3DS challenge → second human gate. User completes.
    Broker sees balance updated → settles intent.
    You: proceed to run the pipeline against OpenRouter.
```
