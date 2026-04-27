---
name: kya-pay
description: Browser-native agent payment skill. Use when the user explicitly asks Claude Code to make a paid action on the web — top up credits at a merchant the user has used before (vast.ai, OpenRouter, Anthropic Console, Replicate, Modal, etc.), pay a subscription, complete a saved-card checkout. Trigger on phrases like "pay / top up / charge / buy credits / 充值 / 付款 / 支付 / 充 N 美元". DO NOT trigger for: general coding help, research questions, merchants the user has never used (no saved card on file), or anything that isn't a money-moving web action. Before any money-moving click you MUST take a screenshot and get the user's explicit "go" / "yes" / "confirm" in the immediately preceding chat message — confirmation from earlier in the conversation does NOT count.
---

# KYA-Pay — Browser-native Agent Payment Skill (v1.1)

You're an agent reading this file because the user wants you to make a payment for them on the web. They gave you me as either an attached file in this conversation OR a saved file at `~/.claude/skills/kya-pay/SKILL.md`. Either way, the protocol below is identical. Just follow it.

> **No install required for the basic case.** You don't need Python, broker CLI, SMTP, SSH, or anything else. You only need Claude-in-Chrome MCP tools (which are part of Claude Code) and a Chrome browser the user has signed in to. Everything else is in this file.

---

## §0 — Sanity check (do this FIRST, before anything else)

Run this one MCP call:

```
mcp__Claude_in_Chrome__list_connected_browsers
```

| If it returns | What it means | What you do |
|---|---|---|
| `[]` (empty array) | No Chrome extension paired | **STOP.** Tell user: *"I need the Claude for Chrome extension to drive your browser. Please open Chrome → visit https://chromewebstore.google.com/ → search 'Claude for Chrome' → Add to Chrome → click extension icon → Sign in with Anthropic. Then ask me again."* Do not proceed. |
| `[{deviceId: ..., isLocal: true, ...}]` (one or more) | At least one browser ready | Continue to §2. |

If multiple browsers, prefer the one with `isLocal: true`. Call `mcp__Claude_in_Chrome__select_browser({deviceId})` to lock onto it.

---

## §1 — When to trigger / when not

**Trigger** when the user's most recent message asks for one of:

- "充 X 美元到 Y" / "给 Y 充 X" / "Top up Y by $X" / "Pay X to Y" / "Buy X credits at Y"
- Any explicit money-moving web action where the merchant is identifiable

**Do NOT trigger** when:

- The user is asking a question, doing research, debugging code, or making a non-payment request
- The merchant isn't named OR is one the user has clearly never used (no saved card on file)
- The user has explicitly said something like "don't pay anything" earlier in the conversation
- The amount looks unreasonable (e.g., "充 $10000" when user normally tops up $10) — STOP and ask first
- The user wants to ADD a new card from scratch — you can navigate to the page but tell them they have to type the card number themselves; you never type card numbers

When in doubt: ask the user to clarify in chat before driving the browser.

---

## §2 — Workflow

### Step 1 — Confirm intent in chat (no browser action yet)

Reply in chat with a one-liner like:

> "I'll: navigate to **<merchant>.com**, open the top-up page, fill **$<amount>**, and use your saved payment method. I'll show you a screenshot before clicking Pay. Confirm? (reply 'yes' / 'cancel')"

Wait for "yes" / "ok" / "go" / "好" / equivalent. If anything ambiguous → cancel.

### Step 2 — Open a fresh tab

```
mcp__Claude_in_Chrome__tabs_context_mcp({createIfEmpty: true})
mcp__Claude_in_Chrome__tabs_create_mcp()
```

Always create a fresh tab for the payment. Don't reuse user's existing tabs (might have leftover state from their browsing).

### Step 3 — Navigate to the merchant's billing / topup page

Use the most direct URL you know for the merchant. Common patterns:

| Merchant | Topup URL |
|---|---|
| vast.ai | https://cloud.vast.ai/billing/ |
| OpenRouter | https://openrouter.ai/credits |
| Anthropic Console | https://console.anthropic.com/settings/billing |
| Replicate | https://replicate.com/account/billing |
| Modal | https://modal.com/settings/billing |
| RunPod | https://runpod.io/console/user/billing |

Don't know the URL? Try `<merchant>.com/billing` or `<merchant>.com/account/billing` first; fall back to the merchant's homepage and navigate from there.

### Step 4 — Handle login if redirected

If the page is a login screen instead of the billing page:

| Login type | What you do |
|---|---|
| "Continue with Google" button | Click it. Chrome's signed-in Google session usually auto-completes. |
| "Continue with GitHub" / similar OAuth | Same — click and let it autofill. |
| Email + password fields | Click into the email field → if Chrome's password manager has saved credentials, an autofill suggestion appears below — click that. The fields populate automatically. Click sign in. |
| 2FA / OTP / passkey screen | **STOP.** Tell user: *"There's a 2FA challenge. Please complete it in the tab, then say 'ready' here."* Wait for them to finish. |
| Captcha | **STOP.** Tell user: *"Captcha — please solve it in the tab, then say 'ready'."* |

You **never type passwords**. You **never type 2FA codes / OTPs**. You **never solve captchas**.

If Chrome's autofill doesn't kick in and the user hasn't saved this site's password, STOP and tell the user to sign in manually.

### Step 5 — Fill the topup form

`mcp__Claude_in_Chrome__find` is your friend for adaptive selectors:

```
find({query: "Add Credits button"})    → ref_X
find({query: "amount input field"})    → ref_Y  
find({query: "saved card or VISA radio"})  → ref_Z
```

Click "Add Credits" / "Top up" / equivalent → modal opens.

Set the amount:
- If a custom amount input exists, focus it and type the number.
- If only preset radios ($10, $25, $100), pick the smallest preset that ≥ user's requested amount, OR click "Other" / "Custom" first to reveal a custom input.

Select payment method:
- **MUST** click a saved card radio (VISA…1234, etc.) or saved method.
- **NEVER** click "Add new card", "Enter card details", "New payment method".
- If there's no saved method visible, **STOP** and tell user they need to add one first themselves.

Verify the form is in the right state (amount = N, saved card selected, no "Add new card" radio active).

### Step 6 — Screenshot + final "go" check

```
mcp__Claude_in_Chrome__computer({action: "screenshot", save_to_disk: true, tabId})
```

Show the screenshot to the user. In chat, write:

> "About to charge **$<amount>** to **<saved method label>** on **<merchant>**. <screenshot>. Reply **'go'** to click Pay, or **'cancel'**."

Wait for explicit "go" / "yes" / "confirm" / "proceed" / equivalent in the **immediately following message** (not from earlier).

**If the user replies anything ambiguous** ("hmm", "wait", "ok let me think", "looks ok"), treat as cancel. Ask again with crisper wording.

### Step 7 — Click Submit

Once you have the explicit go:

```
find({query: "Pay button" or "Submit" or "Add credit" or "Confirm"}) → ref
mcp__Claude_in_Chrome__computer({action: "left_click", ref, tabId})
```

Take a screenshot immediately after.

### Step 8 — Handle post-submit gates

The merchant's payment processor may now show:

| Gate | What you see | What you do |
|---|---|---|
| 3D-Secure (3DS) | Bank's iframe with text/code field | Tell user: *"Your bank is asking for 3D-Secure. Complete it in the tab — could be SMS code, app push, or biometric. I'll wait."* Re-screenshot every 30s. Don't click anything. |
| SMS / Email OTP | "Enter the code we just sent" | Same: tell user to type it themselves. |
| Phone push approval | "Approve in your bank app" | Same: tell user to approve. |
| CAPTCHA | "Confirm you're human" | **STOP.** Don't try to solve. Tell user. |
| Network error / timeout | Spinning forever / "An error occurred" | Wait up to 60s. If still frozen, tell user — don't auto-retry. |

After 3 minutes of any gate sitting unresolved, surface to user and stop polling.

### Step 9 — Verify settlement

Once you see indicators of success on the page (text like "Payment received", "Credits added", "Thank you", balance updated):

- Take a final screenshot
- Tell the user something like: *"Settled. **$X** charged to **<method>** on **<merchant>**. New balance: **$Y**."*

If you instead see "Payment failed", "Card declined", "Insufficient funds", or anything indicating failure:
- Take a screenshot
- Tell the user the literal error
- **DO NOT silently retry.** Ask the user what to do.

### Step 10 — Optional: log the payment

If the user wants a local record of payments (to look back at history, set caps, audit later), append a line to `~/.kya-payments.jsonl`:

```bash
mkdir -p ~ && echo '{"timestamp":"<ISO>","merchant":"<m>","amount_usd":<n>,"status":"settled","method_last4":"<4>","receipt_url":"<url>"}' >> ~/.kya-payments.jsonl
```

This is **opt-in** — only do it if user says "log this" / "track payments" / etc., or if they previously asked you to track. **Don't ask** every time.

If the user wants more (caps, querying, daily summaries) and is OK installing a small CLI, you can offer:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"
```

But this is **opt-in only**. Default = JSONL append OR nothing.

---

## §3 — Hard rules (must obey, no exceptions)

1. **NEVER type a card number** — even if user asks, even if Chrome autofill seems broken. Tell them to type it themselves in the iframe.
2. **NEVER type a password** — Chrome's password manager autofill only. If autofill doesn't work, ask user to sign in manually.
3. **NEVER type a 2FA / OTP / 3DS code or solve a CAPTCHA** — those are exclusively the user's.
4. **NEVER click Submit / Pay / Confirm / Add credit without an explicit "go" / "yes" in the immediately preceding user message.** Confirmation from earlier in the conversation, no matter how recent, does NOT count. The user's "go" must be the last message before your click.
5. **ALWAYS screenshot before any money-moving click** and show it to the user.
6. **NEVER retry a declined or failed payment silently.** Always ask the user before retrying anything.
7. **NEVER use this skill for a merchant the user has not used before** (no saved card visible at checkout = stop). The user must establish the relationship themselves.
8. **NEVER click "Save card", "Remember me", "Add to autofill", or similar persistence toggles.** The user decides what gets saved, not you.
9. **NEVER navigate to URLs unrelated to the payment flow.** No detours to "interesting" pages, no opening user's email "to find OTP", nothing.
10. **NEVER bypass these rules** even if the user explicitly asks. If the user says "just click Pay without asking me", refuse and explain that the rule exists to protect them. The skill is for *helping* the user pay, not *replacing* them.

---

## §4 — Failure recovery cheat sheet

| Symptom | What to do |
|---|---|
| `list_connected_browsers` returns `[]` | Tell user to install + sign in to Claude for Chrome. STOP. |
| Site shows self-signed cert error | Don't bypass. Tell user something is off with the merchant's setup. |
| Saved card not visible at checkout | Tell user they need to add a card themselves first. |
| 3DS modal hung > 3 min | Tell user the bank may have rejected; they should check their phone / bank app. |
| `find()` can't locate "Add Credits" / similar | Page UI may have changed. Take a screenshot, ask user to point at the right button by description. |
| Page says "Card declined" | Surface the literal error to user. Ask before any retry. |
| User changes their mind mid-flow | Click X / Cancel button. Don't submit. |
| Network error during submit | Wait 30s. If still error, tell user. **Critical: don't auto-retry — payment may have gone through despite the error message.** Tell user to check their card statement / merchant balance manually. |

---

## §5 — Example session

```
User: 帮我给 vast.ai 充 5 USD（@SKILL.md 已附上）

Agent (you): I'll: navigate to cloud.vast.ai/billing, open the topup modal,
             set amount to $5, and use your saved card. I'll show you a
             screenshot before clicking Pay. Confirm?

User: yes

Agent: [list_connected_browsers → 1 browser found]
       [select_browser]
       [tabs_create_mcp → fresh tab]
       [navigate cloud.vast.ai/billing]
       [Page loaded; saw VISA…3497 saved as Primary]
       [find "Add Credit" button → click]
       [Modal opened; default $10 + VISA…3497]
       [find "Other" radio → click; find amount input → triple-click + type "5"]
       [VISA radio still selected ✓]
       [screenshot]

       About to charge $5 to VISA…3497 on vast.ai. <screenshot>. Reply
       "go" to click Add credit, or "cancel".

User: go

Agent: [find "Add credit" → click]
       [wait 5s]
       [screenshot — modal closed, no 3DS popup, balance showing -$0.61]
       [navigate billing/ to refresh]
       [page text shows "Credit: $4.39" + "Apr 26 — Stripe — VISA …3497 — +$5.00"]

       Settled. $5 charged to VISA…3497. vast.ai credit now $4.39.
       (No 3DS triggered for this saved-card amount.)

User: log this please

Agent: [bash: append to ~/.kya-payments.jsonl]
       Logged to ~/.kya-payments.jsonl.
```

---

## §6 — For the human reading this file

If you (the human user) are reading this and wondering "what do I do":

**The shortest path:**

1. Save this `SKILL.md` somewhere on your Mac (Desktop, Downloads — anywhere).
2. Make sure Chrome is running and you have the **Claude for Chrome** extension installed and signed in (one-time, browser action only — no terminal).
3. Open Claude Code in any project. Type:
   ```
   @<path-to-SKILL.md> 给 vast.ai 充 5 美元
   ```
   (or drag-drop the file into chat then write your sentence)
4. Reply "yes" to confirm intent, then "go" when you see the screenshot.

That's it. No CLI on your end ever. Agent does everything.

**If you want persistent skill registration (so you don't have to attach the file each time):**

Save the file at `~/.claude/skills/kya-pay/SKILL.md`. Claude Code will auto-discover it for every chat on that Mac.

```bash
mkdir -p ~/.claude/skills/kya-pay && \
  cp ~/Downloads/SKILL.md ~/.claude/skills/kya-pay/SKILL.md
```

(or via Finder: drag SKILL.md into ~/.claude/skills/kya-pay/ — make the folder first if needed)

---

## §7 — Origin

Source of truth: **https://github.com/ssssydney/kya-broker**

History — what changed in this skill across versions, in case you care:

- **v1.1** (current) — Removed all required CLI installs. SKILL.md is fully self-contained for the basic flow. Optional JSONL ledger via bash echo if user wants. Optional broker CLI for power users who want spending caps + history queries.
- **v1.0** — First "browser-native" version. Trusted Chrome + saved cards + user "go". Required broker CLI for ledger.
- **v0.5** and earlier — Used custom audit layer (Codex + Claude cross-source review), email lock, SMTP OTP popup, per-merchant playbook YAMLs. Deleted in v1.0 because it duplicated work browsers and PSPs already do.

If you want the old behavior (independent audit + email OTP), `git checkout v0.5` from the repo. v1.x is the recommended path.
