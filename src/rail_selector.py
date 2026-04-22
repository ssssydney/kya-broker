"""Pick a payment rail for a given intent.

A rail is a named path-to-money. For v0.3.1 the only concrete rail is `crypto`
(MetaMask + crypto.com checkout). `fiat_card` is reserved for v0.4+ and returns
unavailable for now.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .intent import Intent


@dataclass
class Rail:
    name: str
    playbook: str
    available: bool
    reason: str | None = None


class RailUnavailableError(Exception):
    pass


def list_rails(cfg: Config) -> list[Rail]:
    """Enumerate every rail with its availability + which playbook it binds to."""
    result: list[Rail] = []
    for name in cfg.rails:
        if name == "crypto":
            # crypto rail is always "available" in the skill sense — the real
            # precondition (wallet funded, MetaMask installed) is checked at
            # setup time and again at execution time.
            result.append(
                Rail(
                    name="crypto",
                    playbook="vast_topup_crypto.yaml",
                    available=True,
                )
            )
        elif name == "fiat_card":
            result.append(
                Rail(
                    name="fiat_card",
                    playbook="",
                    available=False,
                    reason="fiat rail reserved for v0.4+ (Stripe Issuing)",
                )
            )
        else:
            result.append(
                Rail(
                    name=name,
                    playbook="",
                    available=False,
                    reason=f"unknown rail {name!r}",
                )
            )
    return result


def select_rail(cfg: Config, intent: Intent) -> Rail:
    """Pick the first usable rail for this intent.

    Preference order is taken from `config.rails` (top = highest priority).
    Merchant-specific `preferred_rail` can override.
    """
    merchant = cfg.merchant(intent.merchant)
    preference: list[str] = list(cfg.rails)
    if merchant and merchant.preferred_rail and merchant.preferred_rail in preference:
        preference.remove(merchant.preferred_rail)
        preference.insert(0, merchant.preferred_rail)

    rails = {r.name: r for r in list_rails(cfg)}
    for name in preference:
        r = rails.get(name)
        if r and r.available:
            # Merchant caps: if this merchant restricts this rail, enforce here.
            if merchant and intent.amount_usd > merchant.max_single_topup_usd:
                raise RailUnavailableError(
                    f"amount ${intent.amount_usd:.2f} exceeds merchant cap "
                    f"${merchant.max_single_topup_usd:.2f}"
                )
            return r

    raise RailUnavailableError(
        f"no rail available for merchant {intent.merchant}; "
        f"tried {preference} — check your config.yaml"
    )
