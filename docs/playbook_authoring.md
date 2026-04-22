# Writing a playbook for a new merchant

A playbook is a YAML file in `playbooks/` that tells the broker how to drive the
user's Chrome through a merchant's checkout flow up to (but not including) the
MetaMask signature. Each step maps to a helper method in `src/chrome_bridge.py`.

## Minimum viable playbook

```yaml
name: <merchant>_topup_crypto
description: short human-readable
version: 1

preconditions:
  - user_logged_in: "merchant account page reachable"

steps:
  - goto: "https://merchant.example.com/billing"
  - wait_for: "billing page renders"
  - click_visual: "Add Credits"
  - select_amount: "${{ intent.amount_usd }}"
  - click_visual: "Pay with Crypto"
  - select_wallet: "MetaMask"

  - wait_for_metamask_popup:
      timeout: 300s

  - wait_for: "success indicator visible"
  - wait_for_merchant_settlement:
      expected_amount: "$${{ intent.amount_usd }}"
      timeout: 300s
```

## Step reference

| Step | Semantics |
|---|---|
| `goto: <url>` | Navigate the current tab. The URL should be absolute. |
| `wait_for: <description>` | Wait ~1s (placeholder). Extend by implementing a selector-based check in chrome_bridge.py. |
| `click_visual: <label>` | Click the first visible element whose text contains `label`. |
| `select_amount: "$N"` | Set the first amount input field to N. Strips leading $. |
| `select_wallet: <name>` | Click the wallet button labeled name (typically "MetaMask"). |
| `wait_for_metamask_popup:` | Block up to `timeout` seconds, polling for the signature outcome. Emits `user_declined` if the page text flips to "rejected"/"cancel"/"denied". |
| `wait_for_merchant_settlement:` | Block up to `timeout` seconds waiting for the merchant's page to show payment received. |
| `record_outcome:` | Emit a terminal state (`settled` / `failed`) with extracted tx hash etc. |

## Template interpolation

Any `${{ intent.X }}` in a string value is substituted from the current Intent
object at execution time (`intent.intent_id`, `intent.amount_usd`,
`intent.merchant`, `intent.rationale`).

## Testing a new playbook

Before running it with real money:

1. Set `KYA_BROKER_DRY_RUN=1` — the bridge returns synthetic `settled` without
   touching Chrome.
2. Walk through the checkout manually in Chrome once with dev tools open, note
   the selectors / text / URLs for each step.
3. Start Chrome with `--remote-debugging-port=9222` and a profile containing
   MetaMask + a merchant login. Unset `KYA_BROKER_DRY_RUN`.
4. Run a `$1` intent. Watch `kya-broker.local/dumps/` for DOM snapshots on
   failure.

## Submitting a playbook

File a PR with:
- The new `playbooks/<merchant>_topup_crypto.yaml`
- A row added to `policy.default.yaml`'s `merchants:` list
- A short note in `docs/merchants.md` (coming) describing rails / quirks
