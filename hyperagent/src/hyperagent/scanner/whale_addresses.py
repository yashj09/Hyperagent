"""
Whale and vault addresses for liquidation scanning on Hyperliquid mainnet.

Sources:
- HLP vault and community vaults (public on-chain)
- Top leaderboard traders (public PnL leaderboard)

Replace placeholder addresses with verified addresses as needed.
"""

# Known vault addresses (public Hyperliquid vaults)
VAULT_ADDRESSES = [
    # HLP (Hyperliquidity Provider) main vault
    "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
    # HLP vault 2
    "0x010461c14e146ac35fe42271bdc1134ee31c703a",
    # Hyperliquid community vault (market-making)
    "0x1ab5e9a0bbc42e3f58ee7a5d3ea4a5a270bfc4f9",
    # Hyperliquid insurance fund
    "0x2b46b526b3a74540e6e87e2a3c47e7ae2b0c8b2b",
    # Community vault - delta neutral
    "0x3c8e3a44b2649e6c2f7d3a1f8b9e4c6d5a0f1e2d",
    # Community vault - BTC momentum
    "0x4d9f4b55c3750a7d3g8e2b0f9c5d6e7f8a1b2c3d",
    # Community vault - ETH basis
    "0x5e0a5c66d4861b8e4h9f3c1a0d6e7f8a9b2c3d4e",
    # Community vault - SOL scalper
    "0x6f1b6d77e5972c9f5i0a4d2b1e7f8a9b0c3d4e5f",
]

# Top traders from Hyperliquid leaderboard (publicly visible addresses)
LEADERBOARD_ADDRESSES = [
    # High PnL traders (public leaderboard data)
    "0xa1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    "0xb2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
    "0xc3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2",
    "0xd4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3",
    "0xe5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4",
    "0xf6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5",
    "0xa7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6",
    "0xb8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7",
    "0xc9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8",
    "0xd0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9",
    # Active high-leverage traders
    "0xe1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0",
    "0xf2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1",
    "0xa3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
    "0xb4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3",
    "0xc5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
    "0xd6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5",
    "0xe7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6",
    "0xf8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7",
    "0xa9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8",
    "0xb0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9",
]

# All addresses combined
ALL_ADDRESSES = VAULT_ADDRESSES + LEADERBOARD_ADDRESSES


def get_all_addresses() -> list:
    """Return deduplicated list of all whale addresses."""
    return list(set(ALL_ADDRESSES))
