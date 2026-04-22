# Architecture

KYA-Broker (Know-Your-Agent Broker) is a portable Claude Code skill that lets
an agent autonomously pay merchants on behalf of the user, without ever holding
the user's private keys or passwords. This document explains the pieces and
why they're shaped the way they are.

## Principal diagram

Four principals and what each is responsible for:

| Principal | Identity | Can do | Cannot do |
|---|---|---|---|
| Claude Code (agent) | user's local CC session | propose intents, read ledger status | sign transactions, move money |
| Broker (this skill) | Python process | validate, persist, audit, drive Chrome | sign transactions, bypass MetaMask |
| Auditor (Codex / Claude) | independent model-family | approve / reject intents, write verdicts | influence execution beyond primary verdict |
| Browser + MetaMask | user's Chrome profile | sign transactions (with password) | propose intents, change policy |

No principal has the full chain of capabilities required to move money without
at least one human action. That's the core property the architecture preserves.

## Data flow for one topup

```
agent                broker                 auditor              chrome+metamask         vast
  │  propose_intent  │                         │                         │                │
  │ ───────────────▶ │                         │                         │                │
  │                  │── validate, tier ───────┤                         │                │
  │                  │── audit ──────────────▶ │                         │                │
  │                  │ ◀──── verdict ──────────┤                         │                │
  │  state: audited  │                         │                         │                │
  │ ◀──────────────  │                         │                         │                │
  │                  │── run playbook ────────────────────────────────▶ │                │
  │                  │                         │                         │── navigate ──▶ │
  │                  │                         │                         │ ◀── page ───── │
  │                  │                         │                         │── click,etc.─▶ │
  │                  │                         │                         │  [MetaMask     │
  │                  │                         │                         │   popup]       │
  │                  │                         │                         │ user signs     │
  │                  │                         │                         │── tx ────────▶ │
  │                  │                         │                         │ ◀──settled──── │
  │                  │ ◀── tx hash, receipt ──────────────────────────── │                │
  │  state: settled  │                         │                         │                │
  │ ◀──────────────  │                         │                         │                │
```

## Intent lifecycle

```
        propose_intent
            │
            ▼
       ┌────────┐   audit reject       ┌──────────┐
       │proposed│──────────────────▶   │ rejected │
       └────────┘                      └──────────┘
            │ audit approve
            ▼
       ┌────────┐
       │ audited│
       └────────┘
       │        │
       │ L0     │ L1
       ▼        ▼
  ┌───────┐ ┌───────────────┐
  │execute│ │ awaiting_user │
  └───────┘ └───────────────┘
       │         │ resume (user signed)
       │         ▼
       │    ┌───────┐
       └──▶ │execute│
            └───────┘
             │   │   │   │
 settled ───┘    │   │   └── playbook_broken
           user_declined  failed
```

Terminal states: `settled`, `rejected`, `user_declined`, `failed`, `playbook_broken`, `expired`.

## Why dual auditor?

Claude Code is a Claude agent. If Claude also audits Claude's intents, a single
adversarial input — say, a prompt-injected PDF — has some chance of fooling both
sides: they share training data, both follow similar instruction-following
patterns, both may rationalize suspect arguments the same way.

Codex (GPT family) is trained by a different company on a different corpus.
It's not perfect, but it's the closest thing available to an *independent* opinion.
Using it as the primary auditor is like using two different hash functions in a
security construction — a weakness in one doesn't automatically compromise the other.

**Shadow mode** (`audit.shadow_mode: true`) runs both auditors on every intent
but only the primary vetoes. This costs ~2x but produces a dataset: every intent
now has verdicts from both models, and `broker analyze-audits` computes:
- Agreement rate (how often Codex and Claude say the same thing)
- Per-class disagreement (when they differ, which one is usually right on manual review)
- Latency and cost deltas

This data is why we call v0.3.1 "research-ready" — without both auditors running
side-by-side, we have no way to say whether the cross-model-family choice is actually
buying safety or not.

## Why MetaMask's native popup is the L2 gate

Earlier drafts of this skill had an elaborate authorization stack (L0 auto, L1
passkey, L2 hardware wallet). v0.3 retires that and relies on what's already
there: **MetaMask refuses to sign without an unlocked extension, and unlocking
requires the user's password (or a hardware wallet confirmation)**.

The broker physically cannot bypass this:
1. We don't know the password; it's hashed inside MetaMask's storage.
2. We don't try to click Confirm in the popup — the prompt file `prompts/chrome_agent.md`
   makes this a hard "never do" rule for any Chrome-driving agent.
3. Even if a rogue agent ignored that rule, the OS-level frontmost-app and
   extension-isolation model don't let web content drive the MetaMask popup's
   buttons via scripting.

What's lost: batching many small payments behind one user confirm. What's gained:
every signature shows the user the real amount and recipient. For a skill whose
failure mode is "agent pays too much," that's the right trade.

## File layout

```
~/.claude/skills/kya-broker/           # read-only, git-pullable
├── SKILL.md                           # Claude Code reads this
├── pyproject.toml
├── install.sh / uninstall.sh
├── policy.default.yaml
├── playbooks/*.yaml
├── prompts/*.md
└── src/                               # all Python
    ├── intent.py                      # data model + state machine
    ├── ledger.py                      # SQLite
    ├── auditor/{base,codex,claude,runner}.py
    ├── broker.py                      # orchestrator
    ├── chrome_bridge.py               # CDP + dry-run
    ├── mcp_server.py                  # stdio MCP
    ├── cli.py                         # `broker` CLI
    └── setup_wizard.py                # interactive first-run

~/.claude/skills/kya-broker.local/     # user state, never committed
├── ledger.sqlite
├── config.yaml
├── .env
├── dumps/                             # DOM + screenshots on playbook failure
└── logs/
```

Separating read-only skill code from user state means `git pull` upgrades the
skill without touching ledger or config. Uninstall is `rm -rf` on the skill dir.

## What's *not* in v0.3.1

- Fiat rail (Stripe Issuing). Reserved for v0.4+; the rail_selector returns
  `unavailable` for now.
- Multi-machine coordination. Two brokers pointing at the same wallet can race
  on nonces. Documented as "don't do that."
- Anti-phishing on the user's Chrome profile. If the user installs a malicious
  extension that imitates MetaMask, we can't help. setup.py reminds users to
  use a clean profile.
- Full Claude-in-Chrome MCP integration. The `chrome_bridge.py` has the CDP
  backend scaffolded and a dry-run simulator for testing; wiring it to the
  `claude-in-chrome` MCP tools is pending real-merchant validation.
