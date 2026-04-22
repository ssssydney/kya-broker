"""Pick a payment rail for a given intent.

A rail is a named path-to-money. v0.3.1 ships four:

  * `card`           — credit / debit card via merchant or Stripe checkout
  * `crypto`         — MetaMask / WalletConnect with USDC (or any ERC-20)
  * `email_link`     — magic-link / OTP-gated merchants (some fintech checkouts)
  * `bank_transfer`  — ACH / SEPA wire (rare, usually for B2B)

Each rail resolves to a per-merchant playbook. The user's enrolled payment
methods (from setup) constrain which rails are considered available.

The broker calls `select_rail(cfg, intent)` and gets back a concrete rail
together with the playbook filename to execute. If the user asked for a
specific rail via `intent.rail_hint`, that hint is honored when available.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config, MerchantConfig
from .intent import Intent


class RailUnavailableError(Exception):
    pass


# Canonical rail names that playbooks can claim support for.
SUPPORTED_RAILS = frozenset({"card", "crypto", "email_link", "bank_transfer"})


@dataclass
class Rail:
    name: str
    playbook: str
    available: bool
    reason: str | None = None


def list_rails(cfg: Config) -> list[Rail]:
    """Enumerate every rail declared in config, with an availability hint.

    Availability here is a coarse "is this rail *usable* in principle": we look
    at whether the user has enrolled the underlying payment method. The finer
    check — does this *merchant* have a playbook for this rail? — happens in
    select_rail once we know the merchant.
    """
    enrolled = {m.rail for m in cfg.payment_methods}
    result: list[Rail] = []
    for name in cfg.rails:
        if name not in SUPPORTED_RAILS:
            result.append(
                Rail(name=name, playbook="", available=False, reason=f"unknown rail {name!r}")
            )
            continue
        if name in enrolled:
            result.append(Rail(name=name, playbook="", available=True))
        else:
            hint = {
                "card": "no card enrolled; run `broker setup` and add a card method",
                "crypto": "no crypto wallet enrolled; add MetaMask or WalletConnect in setup",
                "email_link": "no email-link method enrolled",
                "bank_transfer": "bank transfers are not enrolled",
            }[name]
            result.append(Rail(name=name, playbook="", available=False, reason=hint))
    return result


def _resolve_playbook(merchant: MerchantConfig, rail_name: str) -> str | None:
    """Look up the playbook filename the merchant declares for this rail."""
    return merchant.playbooks.get(rail_name)


def select_rail(cfg: Config, intent: Intent) -> Rail:
    """Pick the best usable rail for this intent.

    Resolution order:
      1. If intent.rail_hint is set, try that first.
      2. Iterate cfg.rails (user-defined preference order).
      3. For each, check that (a) the user has enrolled a matching payment
         method, and (b) the merchant has a playbook for this rail.
    """
    merchant = cfg.merchant(intent.merchant)
    if merchant is None:
        raise RailUnavailableError(
            f"merchant {intent.merchant!r} not in allowlist (config.yaml → merchants)"
        )

    enrolled = {m.rail for m in cfg.payment_methods}

    candidate_order: list[str] = []
    if intent.rail_hint and intent.rail_hint in cfg.rails:
        candidate_order.append(intent.rail_hint)
    for r in cfg.rails:
        if r not in candidate_order:
            candidate_order.append(r)

    # Move merchant's declared preferred rail up if still unranked
    if merchant.preferred_rail in candidate_order:
        candidate_order.remove(merchant.preferred_rail)
        # keep hint at position 0 if present; otherwise preferred goes first
        insert_at = 1 if intent.rail_hint and intent.rail_hint != merchant.preferred_rail else 0
        candidate_order.insert(insert_at, merchant.preferred_rail)

    rejections: list[str] = []
    for name in candidate_order:
        if name not in SUPPORTED_RAILS:
            rejections.append(f"{name}: unknown rail")
            continue
        if name not in enrolled:
            rejections.append(f"{name}: user has no matching payment method enrolled")
            continue
        pb = _resolve_playbook(merchant, name)
        if not pb:
            rejections.append(f"{name}: merchant has no playbook for this rail")
            continue
        if intent.amount_usd > merchant.max_single_topup_usd:
            raise RailUnavailableError(
                f"amount ${intent.amount_usd:.2f} exceeds merchant cap "
                f"${merchant.max_single_topup_usd:.2f}"
            )
        return Rail(name=name, playbook=pb, available=True)

    raise RailUnavailableError(
        f"no rail available for merchant {intent.merchant!r}. Tried: "
        + "; ".join(rejections)
    )
