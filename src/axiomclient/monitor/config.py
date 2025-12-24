"""
Configuration constants for the Axiom Monitor.
"""

# Mapping of WebSocket field indices to PairItem attribute names
# These indices come from the Axiom Trade WebSocket pulse messages
FIELD_MAP = {
    0: "pair_address",
    1: "token_address",
    2: "deployer_address",
    3: "token_name",
    4: "token_ticker",
    6: "token_decimals",
    7: "protocol",
    19: "market_cap_sol",  # Latest market cap value
    21: "initial_liquidity_sol",
    22: "initial_liquidity_token",
    23: "num_txn",
    24: "num_buys",
    25: "num_sells",
    26: "bonding_curve_percent",
    33: "migrated_tokens",
    34: "created_at",
    39: "dev_wallet_funding",  # Developer wallet funding information
    41: "dev_tokens",
}

# Configuration constants
CLEANUP_INTERVAL_SECONDS = 5
BATCH_SIZE = 1000
MAX_DEV_TOKENS_THRESHOLD = 20  # Pairs with more dev tokens are ignored
PAIR_AGE_THRESHOLD_SECONDS = 20  # How old before persisting to DB
