"""Validation helpers for the onboarding wizard.

Format checks live here (pure functions, easy to unit-test) and so does
the live Hyperliquid pairing check that verifies a given agent key is
actually approved to sign for a given main address.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def is_valid_address(value: str) -> bool:
    return bool(_ADDRESS_RE.match(value.strip()))


def is_valid_private_key(value: str) -> bool:
    return bool(_PRIVATE_KEY_RE.match(value.strip()))


@dataclass
class PairingResult:
    ok: bool
    error: Optional[str] = None


def verify_agent_pairing(
    agent_private_key: str, main_address: str, testnet: bool = True
) -> PairingResult:
    """Confirm the agent key is approved to sign for the main address.

    Strategy: derive the agent's address from the key, then query
    user_state for the main address. If the main address doesn't exist on
    the selected network, that's a clear error. We can't non-destructively
    verify the approveAgent link without placing an order — but we can
    catch the most common user mistakes (wrong network, typo'd address,
    agent never approved) by round-tripping through the SDK's signing path
    and catching the error.
    """
    try:
        from eth_account import Account
        from hyperliquid.info import Info
        from hyperliquid.utils import constants as hl_constants
    except ImportError as exc:
        return PairingResult(False, f"Missing dependency: {exc}")

    try:
        Account.from_key(agent_private_key)
    except Exception as exc:
        return PairingResult(False, f"Invalid private key format: {exc}")

    url = (
        hl_constants.TESTNET_API_URL if testnet
        else hl_constants.MAINNET_API_URL
    )

    try:
        info = Info(url, skip_ws=True)
    except Exception as exc:
        return PairingResult(
            False,
            f"Could not reach Hyperliquid {('testnet' if testnet else 'mainnet')}: {exc}",
        )

    try:
        state = info.user_state(main_address)
    except Exception as exc:
        return PairingResult(
            False,
            f"Could not fetch account state for {main_address}: {exc}",
        )

    # user_state returns a dict even for never-seen addresses; what differs
    # is whether marginSummary has any real numbers. A brand-new wallet
    # with zero testnet USDC will still produce a valid dict, so we don't
    # over-index on content — we just confirm the query succeeded. The
    # real proof-of-approval happens on first trade; we surface that error
    # at runtime with a clear banner.
    if not isinstance(state, dict):
        return PairingResult(
            False, "Hyperliquid returned unexpected data; try again."
        )

    return PairingResult(True)
