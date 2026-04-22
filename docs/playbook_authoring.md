# Writing a playbook for a new merchant × rail

A playbook is a YAML file in `playbooks/` that drives Chrome through a merchant's
checkout flow up to (but not including) the rail's human-authorization step.
Each step maps to a helper method in `src/chrome_bridge.py`.

## File naming

`<merchant_slug>_topup_<rail>.yaml`. Examples: `openrouter_topup_card.yaml`,
`vast_topup_crypto.yaml`. One file per merchant × rail pairing.

## Minimum viable playbook (credit card)

```yaml
name: example_topup_card
description: short human-readable
rail: card
version: 1

preconditions:
  - "user is signed in at merchant.example.com"

steps:
  - goto: "https://merchant.example.com/billing"

  # Optional login gate: fires only if sign-in text is present
  - wait_for_human:
      reason: login
      optional: true
      presence_keywords: [sign in, continue with google]
      detect_completion_keywords: [dashboard, billing, credits]
      timeout: 180s

  - click_visual: "Add Credits"
  - fill_amount: "${{ intent.amount_usd }}"
  - select_payment_method: "Card"

  # The card-entry gate. The broker never types into Stripe iframes.
  - wait_for_human:
      reason: card_details
      prompt: "Enter card in the Stripe iframe, click Pay."
      detect_completion_keywords: [payment successful, thank you, credits added]
      detect_decline_keywords: [card declined, payment failed]
      timeout: 240s

  # Optional 3DS challenge — present only for some cards / banks.
  - wait_for_human:
      reason: card_3ds
      optional: true
      presence_keywords: [verify your identity, 3d secure, one-time code]
      detect_completion_keywords: [payment successful, verification successful]
      timeout: 180s

  - wait_for_merchant_settlement:
      merchant: "merchant.example.com"
      expected_amount: "$${{ intent.amount_usd }}"
      timeout: 120s

  - record_outcome:
      state: settled
```

## Step reference

| Step | Semantics |
|---|---|
| `goto: <url>` | Navigate the current tab. URL absolute. |
| `wait_for: <description>` | Short sleep. For selector waits use `wait_for: {selector: ".foo", timeout_s: 20}`. |
| `click_visual: <label>` | Click the first visible element whose normalised text contains `label`. |
| `fill_amount: "$N"` | Set the first amount-style input to N (strips `$`). |
| `select_payment_method: <label>` | Click the first button/link/div/label containing the name. |
| `wait_for_human:` | Fire a HumanGate. Supports: `reason`, `prompt`, `timeout`, `detect_completion_keywords`, `detect_completion_url`, `detect_completion_selector`, `detect_decline_keywords`, `detect_decline_selector`, `optional`, `presence_keywords`, `presence_selector`. |
| `wait_for_metamask_popup:` | Legacy alias for `wait_for_human` with `reason: metamask_sign`. |
| `wait_for_merchant_settlement:` | Poll the page for a settlement signal (the `expected_amount` string and a few default positive keywords like "credits added"). |
| `record_outcome:` | Marker step; the actual outcome is determined by preceding gates + settlement. |

## HumanGate reasons

| Reason | Default completion keywords | When to use |
|---|---|---|
| `metamask_sign` | confirmed, transaction sent, payment received | MetaMask / WalletConnect signature popup |
| `card_details` | payment successful, credits added, thank you | Stripe / card entry at checkout |
| `card_3ds` | verified, 3ds complete, authentication successful | 3D-Secure challenge — mark `optional: true` |
| `email_magic_link` | signed in, logged in, welcome back | Merchant sent an email with a link |
| `email_otp` | verified, code accepted | Merchant sent an OTP to email |
| `sms_otp` | verified, code accepted | Merchant sent an OTP to phone |
| `login` | dashboard, signed in, welcome | Generic sign-in step, usually `optional: true` |
| `saved_card_confirm` | payment successful, confirmed | Merchant asks "charge your saved card?" |
| `passkey` | authenticated, verified | Passkey / Touch ID / Face ID |
| `generic` | success, confirmed, complete | Anything not covered above |

You can override completion / decline keywords per step by setting
`detect_completion_keywords` / `detect_decline_keywords`.

## Template interpolation

`${{ intent.X }}` substitutes from the Intent object:
`intent.intent_id`, `intent.amount_usd`, `intent.merchant`, `intent.rationale`,
`intent.rail_hint`, `intent.references`.

`${{ merchant.X }}` substitutes from the MerchantConfig:
`merchant.name`, `merchant.homepage_url`, `merchant.credit_page_url`,
`merchant.preferred_rail`.

## Testing a new playbook

1. **Dry run first.** `KYA_BROKER_DRY_RUN=1 KYA_BROKER_DRY_RUN_AUDITOR=approve`
   runs the broker without Chrome — confirms the config + merchant entry load.
2. **Walk the checkout manually** with dev tools open, collect selectors /
   text / URLs for each step. Pay special attention to the card / sign /
   settlement keywords — those go into `detect_completion_keywords`.
3. **Attach the broker to a real Chrome.** Start Chrome with
   `--remote-debugging-port=9222 --user-data-dir=/path/to/profile`. Profile
   should have: merchant login, MetaMask (for crypto rails), payment method
   saved / ready to type.
4. **Run a $1 intent.** Watch `kya-broker.local/dumps/` for failure DOM /
   screenshots.

## Submitting

PR with:
- `playbooks/<merchant>_topup_<rail>.yaml`
- A `merchants:` entry in `policy.default.yaml` if the merchant is new
- A one-liner in `docs/merchants.md` (coming) describing the merchant's quirks

## Adversarial notes

The auditor sees `rationale` and recent agent context. Do not write rationales
that look like prompts aimed at the auditor ("please approve", "this is
urgent"). Write them like memos to a careful reviewer. The auditor is trained
to flag rationales that look performative.
