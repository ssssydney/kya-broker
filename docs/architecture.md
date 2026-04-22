# Architecture

KYA-Broker (Know-Your-Agent Broker) is a Claude Code skill that lets the agent
autonomously pay merchants on behalf of the user, without ever holding the
user's private keys, card numbers, or passwords. This doc explains the pieces
and the trade-offs.

## Principals

| Principal | Can do | Cannot do |
|---|---|---|
| Claude Code (agent) | propose intents, read ledger status | sign transactions, enter card numbers, move money |
| Broker (this skill) | validate, persist, audit, drive Chrome up to the human gate | sign, type card data, bypass gates |
| Auditor (Codex / Claude) | approve / reject intents, write verdicts | influence execution beyond the primary verdict |
| Browser + human | sign in, enter cards, sign transactions, complete 3DS / OTP / magic links | propose intents, change policy |

No single principal can move money without at least one human action at the
rail's native authorization step.

## The HumanGate primitive

The biggest design shift in v0.4: every rail collapses to the same primitive.

```python
class HumanGateRequest:
    reason: "metamask_sign" | "card_details" | "card_3ds" | "email_magic_link"
          | "email_otp" | "sms_otp" | "login" | "saved_card_confirm" | "passkey" | "generic"
    prompt: str                     # user-facing explanation
    timeout_seconds: int
    on_completion: predicate        # checks DOM / URL / selectors
    on_decline: predicate
    optional: bool                  # skip if presence_check says "not present"
    presence_check: predicate
```

The broker calls `human_gate.wait_for_human(request)` and gets back
`completed | declined | timeout | skipped`. Notification channels (terminal
panel, macOS banner, say-aloud, custom callable) are pluggable.

Every playbook step of the form `wait_for_human:` becomes one `HumanGateRequest`. The legacy `wait_for_metamask_popup` step from v0.3.1 is a thin alias.

## Playbook model

A playbook is a YAML file that drives Chrome through a specific merchant × rail combination. For example, OpenRouter card vs OpenRouter crypto are two different files. The playbook schema:

```yaml
name: openrouter_topup_card
rail: card

preconditions:
  - human-readable assertion

steps:
  - goto: <url>                   # navigate
  - wait_for: <description>       # placeholder sleep / DOM wait
  - click_visual: <label>         # click element by visible text
  - fill_amount: <usd>            # set amount input
  - select_payment_method: <name> # click method radio / button
  - wait_for_human:               # HumanGate — see above
      reason: <reason>
      prompt: <prompt>
      detect_completion_keywords: [...]
      detect_decline_keywords: [...]
      timeout: 240s
      optional: false
      presence_keywords: [...]    # only used when optional=true
  - wait_for_merchant_settlement: # poll until balance updates
      expected_amount: "$N"
      timeout: 300s
  - record_outcome:
      state: settled
```

Template placeholders like `${{ intent.amount_usd }}` are interpolated from the
Intent at execution time. Chrome-specific details (CSS selectors, XPath) are
hidden behind the step types — the playbook stays merchant-level.

## Intent lifecycle

```
            propose_intent
                │
                ▼
          ┌──────────┐   audit reject    ┌──────────┐
          │ proposed │ ──────────────▶  │ rejected │
          └──────────┘                  └──────────┘
                │ audit approve
                ▼
          ┌──────────┐
          │ audited  │
          └──────────┘
           │        │
           │ L0     │ L1
           ▼        ▼
      ┌──────────┐ ┌──────────────┐
      │ executing│ │awaiting_user │
      └──────────┘ └──────────────┘
                        │ resume (user confirmed)
                        ▼
                  ┌──────────┐
                  │executing │
                  └──────────┘
                  │   │   │   │
         settled  │  user_declined
                  │   │
                  │ failed / playbook_broken
```

Terminal states: `settled`, `rejected`, `user_declined`, `failed`, `playbook_broken`, `expired`.

## Rail selection

`select_rail(cfg, intent)` walks candidates in this order:

1. `intent.rail_hint` if set and the user has a matching enrolled method.
2. Merchant's declared `preferred_rail`.
3. `cfg.rails` (user-defined preference list), filtered to rails the user has enrolled AND the merchant has a playbook for.

If none match, raises `RailUnavailableError` with a human-readable breakdown
of why each candidate was rejected.

## Why dual auditor?

An agent that's vulnerable to prompt injection may also be able to fool an
auditor drawn from the same model family. Codex (GPT family) and Claude
(Anthropic family) are trained on overlapping but distinct corpora; using
Codex as primary + Claude as shadow is the closest thing to a cross-source
security review.

Shadow mode (`audit.shadow_mode: true`) runs both models on every intent for
A/B data. `broker analyze-audits` computes per-intent verdict agreement and
can export CSV for offline analysis.

## Why the human gate is physically unbypassable

Earlier drafts had elaborate L0/L1/L2 passkey stacks. v0.3 retired those in
favor of "whatever the rail already makes the human do":

- **Credit card:** Stripe's iframe is sandboxed and only accepts input from
  the user's keyboard / autofill; the broker cannot type into it (cross-origin)
  and cannot click Pay without the card being filled.
- **3DS:** the bank's challenge frame is a second sandbox. Same story.
- **MetaMask:** the extension popup is a browser-level UI outside the page's
  scripting surface.
- **Email magic-link:** the link goes to a URL only the user's inbox holds.
  Even if the broker had read access to the inbox (it doesn't), the flow is
  designed around the human clicking.
- **SMS OTP:** the OTP is on a second device.

In each case, the "attacker model" (a compromised or prompt-injected Claude Code) cannot complete the authorization. The broker presents the moment; the user (or no one) decides.

## File layout

```
~/.claude/skills/kya-broker/           # read-only, git-pullable
├── SKILL.md
├── policy.default.yaml
├── playbooks/
│   ├── openrouter_topup_card.yaml
│   ├── openrouter_topup_crypto.yaml
│   ├── vast_topup_crypto.yaml
│   ├── vast_topup_card.yaml
│   └── anthropic_topup_card.yaml
├── prompts/
│   ├── audit_system.md
│   ├── audit_codex.md
│   ├── audit_claude.md
│   └── chrome_agent.md
└── src/
    ├── intent.py              # data model + state machine (+ rail_hint)
    ├── ledger.py              # SQLite, schema v3 adds rail_hint column
    ├── auditor/…              # Codex + Claude + runner
    ├── broker.py              # orchestrator
    ├── chrome_bridge.py       # CDP + dry-run; speaks HumanGate
    ├── human_gate.py          # HumanGate primitive + notifiers
    ├── rail_selector.py       # now rail-agnostic
    ├── config.py              # payment_methods, merchants.playbooks map
    ├── mcp_server.py          # stdio MCP
    ├── cli.py                 # `broker` CLI
    └── setup_wizard.py        # interactive enrollment

~/.claude/skills/kya-broker.local/     # user state, never committed
├── ledger.sqlite
├── config.yaml
├── .env
├── dumps/       # DOM + screenshots on playbook failure
└── logs/
```

## What's NOT in v0.4

- Bank transfer (ACH / SEPA). Reserved for v0.5+.
- Bulk / recurring topups without per-intent audit. The cost / benefit doesn't
  favor auto-recurring for a skill that aims to surface every spend.
- Multi-machine coordination with the same card / wallet. Doable but out of
  scope; keep one broker per payment method.
- Anti-phishing on the user's Chrome profile. If you install malicious
  extensions, we can't protect you — setup reminds you to use a clean profile.
