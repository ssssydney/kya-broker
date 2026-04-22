---
name: kya-broker
description: Autonomous payment skill for research workflows. Triggers when user wants Claude Code to run GPU experiments on vast.ai or similar platforms, and allows Claude Code to autonomously top up credits via user's MetaMask wallet. Only triggers when user explicitly mentions running an experiment, needing GPU compute, reproducing a paper on GPUs, or paying a cloud merchant. Do NOT trigger for general coding questions, local testing, or when an instance is already provisioned.
---

# KYA Broker — Agent Payment Skill

You help the user run paid compute workflows on vast.ai and similar GPU-rental merchants. When the workflow needs credit that the user doesn't currently have, you use this skill's CLI to propose a payment intent; an independent auditor (Codex by default) reviews it; and the final authorization happens in the user's MetaMask browser extension — you never see the private key and cannot bypass that step.

## When to use this skill

Use it when the user asks you to:

- Reproduce a paper / run an experiment / train a model that needs GPUs and no instance is already running
- Set up cloud compute that requires paid credit
- Delegate a paid task to an agent pipeline (topup + launch + SSH + run)

Do NOT use it when:

- The user is asking a general coding / research / reading question
- An instance is already provisioned and has credit
- The merchant is not in `policy.yaml`'s allowlist (ask the user before adding one)

## Workflow

1. **Read the request.** Pull out: the paper / code / experiment spec, dataset size, expected duration, any GPU preferences.
2. **Estimate the job.** How many GPUs, which class (A100 / H100 / 4090), how many hours end-to-end. Estimate the vast.ai dollar cost with a small buffer (15-25%).
3. **Check balance.** Run `broker check-balance`. If vast credit already covers the job, skip to step 6.
4. **Propose intent.** Call `broker propose-intent` with a JSON payload (schema below). The broker will:
   - Validate against `policy.yaml` thresholds
   - Run audit (Codex, Claude, or both per user config)
   - If ≤ L0 ceiling and audit approves: auto-execute via Chrome automation
   - If L0 < amount ≤ L1 ceiling: audit must approve, then MetaMask popup asks the user to sign
   - If amount > L1 ceiling: broker halts and asks you to surface the intent to the user yourself
5. **Wait for settlement.** Watch `broker status <intent_id>` until state is `settled` or `failed`. Typical crypto topup settles in 30-120 seconds after user signs.
6. **Launch + run.** Use the returned vast.ai credentials / instance info to SSH in and execute the experiment.

Never ask the user to manually copy-paste payment details into a webpage — that defeats the purpose of the skill. Never suggest the user open vast.ai themselves to top up. Always go through `broker propose-intent`.

## CLI reference

- `broker propose-intent <path-to-intent.json>` — submit a payment intent. Prints intent_id and initial state.
- `broker status <intent_id>` — poll the state machine. States: `proposed → audited → awaiting_user → executing → settled | failed | user_declined | playbook_broken`.
- `broker check-balance` — MetaMask USDC balance on configured chain + vast.ai current credit + today's / this-month's spend.
- `broker get-history [--limit N] [--format json]` — recent intents and outcomes.
- `broker analyze-audits [--since DATE]` — only when shadow_mode is enabled; dumps Codex-vs-Claude verdict comparison.

All commands also speak MCP when the broker is registered as an MCP server; the tool names map 1:1 (`propose_intent`, `check_balance`, `get_history`, `get_status`).

## Intent schema (v1.0)

```json
{
  "intent_id": "uuid-v4 — OMIT, broker fills in",
  "merchant": "vast.ai",
  "amount_usd": 10.00,
  "rationale": "Running 2h attention-sink ablation on 2x H100 per Xiao et al. 2023 Table 3 setup",
  "references": ["papers/attention-sink.pdf", "scripts/run_ablation.py"],
  "estimated_actual_cost_usd": 8.50
}
```

The auditor reads `rationale` and `references` and cross-checks them against your recent conversation context. Prompt-injection attempts on your context will be visible to the auditor as rationale/context mismatch, which triggers a reject.

## Hard rules

- Never generate an intent with `amount_usd > policy.l1_ceiling_usd` without explicitly asking the user first; the broker will refuse anyway but it wastes a round-trip.
- Never put credentials (API keys, wallet seeds) into intent `rationale` or `references` — those fields are sent to the auditor, not secret.
- Never retry a `user_declined` intent silently — the user said no; ask them why before proposing again.
- Never suggest the user disable audit or raise thresholds mid-workflow. If a legit experiment needs more money, bring it up as a separate decision before starting work.
- If `broker status` returns `playbook_broken`, stop. Do NOT attempt to complete the payment through some other path. Tell the user and wait for them.

## Example: reproducing a paper

User: "Reproduce the attention-sink finding from this paper."

1. You: Read paper, see it uses 2 × H100 for ~4h. Estimate $12-16.
2. You: `broker check-balance` → vast credit $1.20. Need topup.
3. You: Write `intent.json` with `amount_usd: 15.00`, `rationale` citing paper Table 3, `references: ["paper.pdf"]`.
4. You: `broker propose-intent intent.json` → returns `intent_id=abc-123`, state=`audited` (approved), state advances to `awaiting_user`.
5. User sees MetaMask popup in Chrome, reviews amount + recipient, signs.
6. You: poll `broker status abc-123` until `settled`.
7. You: SSH to returned vast instance, `git clone`, run, report results.

## Failure modes to recognize

- `state=user_declined` — user rejected in MetaMask. Do not retry; ask them first.
- `state=playbook_broken` — vast UI changed or MetaMask popup didn't appear. Dump is in `kya-broker.local/dumps/`. Surface to user.
- `state=failed` with `reason=rail_unavailable` — try a different rail if user has configured one; else report and stop.
- Audit rejected with concern "rationale mismatch" — your rationale didn't match context. Re-read the conversation, don't just paraphrase the intent differently.
