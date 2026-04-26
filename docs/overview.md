# KYA-Broker — version history

## v1.0 (current, browser-native)

**Released:** 2026-04-26
**Tag:** `main` (and future `v1.0`)

A Claude Code skill that drives the user's Chrome via the Claude-in-Chrome MCP to complete payment workflows. ~300 lines of Python. No audit, no OTP, no playbooks — just trust Chrome + the merchant's checkout, and gate every money-moving click on the user's "yes" in chat.

See [README.md](../README.md) and [architecture.md](architecture.md) for details.

## v0.5 (archived)

**Tag:** `v0.5`

Dual-auditor (Codex + Claude shadow) + write-once email lock + SMTP OTP via local-HTTP popup window + per-merchant playbooks. Driven by raw CDP. ~3500 lines of Python.

Why archived: see [migration.md](migration.md). Short version — it was reinventing infrastructure that browsers and payment processors already provide. The cost (3500 lines, SMTP setup, Codex install, brittle selectors) didn't justify the marginal safety benefit.

You can still check it out:
```bash
git checkout v0.5
```

## v0.3 → v0.4 → v0.5 (deprecated)

- **v0.3** (Apr 2026) — Portable Python skill, MetaMask popup as L2 authorization gate. Crypto-only. Single auditor.
- **v0.3.1** — Added Codex + Claude dual auditor with shadow-mode A/B for research.
- **v0.4** — Generalized rails: card / crypto / email_link / 3DS / OTP all routed through a unified `HumanGate` primitive.
- **v0.5** — Added local-HTTP popup window, write-once email lock with SHA256 tamper detection, broker-issued email-OTP floor before any browser drive.

All preserved in git history. None are recommended for new use; v1.0 is the supported path.

## Ancient (deprecated, not in this repo)

- **v0.1** (early 2026) — API-first design: agent calls Stripe Issuing directly with virtual-card credentials. Abandoned because agent held card data — wrong trust boundary.
- **v0.2** — Browser-level human impersonation + WebAuthn + content-script interceptor. Abandoned because architecture was too heavy (4 principals, complex coordination).
