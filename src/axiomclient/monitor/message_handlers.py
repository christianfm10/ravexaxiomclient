"""
WebSocket message handlers for the Axiom Monitor.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Any

from axiomclient.models import DevWalletFunding, PairItem
from axiomclient.monitor.config import FIELD_MAP
from axiomclient.monitor.state import pair_state
from axiomclient.monitor.utils import is_timestamp_older_than

logger = logging.getLogger(__name__)


def initialize_market_cap_fields(pair_item: PairItem) -> None:
    """
    Initialize market cap tracking fields for a new pair.

    ## Parameters
    - `pair_item`: PairItem to initialize (modified in place)

    ## Design Notes
    When a new pair arrives, we initialize three market cap fields:
    - first_market_cap_sol: The first value we see (never changes)
    - high_market_cap_sol: Highest value seen (updated when new high)
    - market_cap_sol: Latest value (always updated)
    """
    if pair_item.market_cap_sol is not None:
        pair_item.first_market_cap_sol = pair_item.market_cap_sol
        pair_item.high_market_cap_sol = pair_item.market_cap_sol


def should_accept_new_token(content: Dict[str, Any]) -> bool:
    """
    Determine if a new token should be tracked.

    ## Parameters
    - `content`: Message content from WebSocket

    ## Returns
    - `True` if token should be tracked
    - `False` otherwise

    ## Filtering Rules
    1. Only accept "Pump V1" protocol tokens
    2. Reject Mayhem tokens (high risk/volatility)
    3. Reject Offchain tokens
    """
    protocol = content.get("protocol", "").lower()
    if protocol != "pump v1":
        return False

    # Check for Mayhem flag
    protocol_details = content.get("protocol_details", {})
    if protocol_details and protocol_details.get("isMayhem", False):
        return False
    if protocol_details and protocol_details.get("isOffchain", False):
        return False

    return True


async def handle_new_token_message(message: Dict[str, Any]) -> None:
    """
    Process incoming new token creation messages from WebSocket.

    ## Parameters
    - `message`: WebSocket message containing new token data

    ## Processing Steps
    1. Extract and validate message content
    2. Apply filtering rules (protocol, Mayhem check)
    3. Create PairItem and initialize market cap fields
    4. Store in memory state

    ## Error Handling
    Catches all exceptions to prevent one bad message from crashing
    the entire monitoring system. Logs errors with full traceback.
    """
    try:
        content = message.get("content", {})
        pair_address = content.get("pair_address", "unknown")

        # Apply filtering rules
        if not should_accept_new_token(content):
            return

        # Check if pair already exists (might have partial data from early update)
        existing_pair = pair_state.get(pair_address)

        # Create pair item from message
        pair_item = PairItem(**content)

        # Initialize market cap tracking
        initialize_market_cap_fields(pair_item)

        # Preserve dev_wallet_funding if it arrived earlier
        if existing_pair and existing_pair.dev_wallet_funding:
            data = pair_item.model_dump()
            data["dev_wallet_funding"] = existing_pair.dev_wallet_funding
            pair_item = PairItem.model_validate(data)
            logger.info(
                f"Updated pair with early funding data: {pair_address} | "
                f"Funding: {existing_pair.dev_wallet_funding.amount_sol} SOL"
            )

        # Store in memory
        pair_state[pair_address] = pair_item

        logger.info(
            f"New token tracked: {pair_address} | "
            f"Name: {pair_item.token_name} | "
            f"Ticker: {pair_item.token_ticker}"
        )

    except Exception as e:
        logger.error(f"Failed to handle new token message: {e}", exc_info=True)


def update_market_cap_fields(data: Dict[str, Any], new_market_cap: float) -> None:
    """
    Update all three market cap fields with proper logic.

    ## Parameters
    - `data`: Pair data dictionary (modified in place)
    - `new_market_cap`: New market cap value from WebSocket

    ## Update Logic
    - `first_market_cap_sol`: Set only if currently None (first value)
    - `high_market_cap_sol`: Update only if new value is higher
    - `market_cap_sol`: Always update (latest value)
    """
    # Set first value if not already set
    if data.get("first_market_cap_sol") is None:
        data["first_market_cap_sol"] = new_market_cap

    # Update high if new value exceeds current high
    current_high = data.get("high_market_cap_sol")
    if current_high is None or new_market_cap > current_high:
        data["high_market_cap_sol"] = new_market_cap

    # Always update latest value
    data["market_cap_sol"] = new_market_cap


def should_skip_field_update(
    field_index: int, new_value: Any, pair_data: Dict[str, Any]
) -> bool:
    """
    Determine if a field update should be skipped based on business rules.

    ## Parameters
    - `field_index`: WebSocket field index
    - `new_value`: New value for the field
    - `pair_data`: Current pair data

    ## Returns
    - `True` if update should be skipped
    - `False` if update should be applied

    ## Skip Rules
    1. Liquidity/transaction fields: Skip if pair is older than 6 seconds
       (prevents stale data from overwriting recent values)
    2. Bonding curve: Skip if new value is lower than current
       (bonding curve percentage should only increase)
    """
    created_at = pair_data.get("created_at", "")

    # Skip liquidity/transaction updates for old pairs
    if field_index in [21, 23, 24, 25]:  # liquidity_sol, num_txn, num_buys, num_sells
        if is_timestamp_older_than(created_at, seconds=6):
            return True

    # Skip bonding curve decreases (should only increase)
    if field_index == 26:  # bonding_curve_percent
        current_value = pair_data.get("bonding_curve_percent")
        if current_value is not None and new_value < current_value:
            return True

    return False


async def process_pair_update_message(message: List[Any]) -> None:
    """
    Process type 1 pulse messages (incremental pair updates).

    ## Parameters
    - `message`: List containing [msg_type, pair_address, changes]

    ## Message Structure
    - `msg_type`: Always 1 for pair updates
    - `pair_address`: Address of the pair being updated
    - `changes`: List of [field_index, new_value] tuples

    ## Algorithm
    1. Look up pair in memory state
    2. If not found, log and skip (may not be tracked yet)
    3. For each field change:
       - Map field index to attribute name
       - Apply business rules (skip if needed)
       - Handle special cases (market cap, funding)
       - Update pair data
    4. Validate and store updated pair

    ## Error Handling
    Comprehensive exception handling to prevent message processing
    failures from affecting other pairs or stopping the monitor.
    """
    try:
        msg_type, pair_address, changes = message
        pair_item = pair_state.get(pair_address)

        # Pair not in memory - may not be tracked yet
        if pair_item is None:
            # Check if this update contains dev_wallet_funding
            funding_data = None
            for field_index, new_value in changes:
                if field_index == 39:  # dev_wallet_funding
                    funding_data = new_value
                    break

            # If funding arrived before new_token message, create partial entry
            if funding_data:
                try:
                    dev_wallet_funding = DevWalletFunding.model_validate(funding_data)
                    # Create minimal PairItem with funding and current timestamp
                    # Format: 2025-12-19T20:03:01.517Z (milliseconds + Z suffix)
                    now = datetime.now(timezone.utc)
                    created_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    partial_pair = PairItem(
                        pair_address=pair_address,
                        dev_wallet_funding=dev_wallet_funding,
                        created_at=created_at,
                    )
                    pair_state[pair_address] = partial_pair
                    logger.info(
                        f"Created partial pair with early funding: {pair_address} | "
                        f"Funding: {dev_wallet_funding.amount_sol} SOL"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to create partial pair for {pair_address}: {e}"
                    )
            return

        # Convert to dict for easier manipulation
        data = pair_item.model_dump()
        updated_fields: List[str] = []

        # Process each field change
        for field_index, new_value in changes:
            field_name = FIELD_MAP.get(field_index)
            if field_name is None:
                continue  # Unknown field index

            # Apply business rules
            if should_skip_field_update(field_index, new_value, data):
                continue

            # Special handling for specific fields
            if field_index == 39:  # dev_wallet_funding
                data[field_name] = DevWalletFunding.model_validate(new_value)
                updated_fields.append(field_name)

            elif field_index == 19:  # market_cap_sol
                update_market_cap_fields(data, new_value)
                updated_fields.append(field_name)

            else:  # Standard field update
                data[field_name] = new_value
                updated_fields.append(field_name)

        # Validate and update pair in state
        pair_state[pair_address] = PairItem.model_validate(data)

        if updated_fields:
            logger.debug(f"Updated pair {pair_address}: {', '.join(updated_fields)}")

    except Exception as e:
        logger.error(f"Failed to process pair update: {e}", exc_info=True)


async def process_new_pairs_message(message: List[Any]) -> None:
    """
    Process type 2 pulse messages (bulk new pair announcements).

    ## Parameters
    - `message`: List containing [msg_type, pair_data_array]

    ## Message Structure
    The pair_data_array contains all pair fields in specific positions:
    - Position 0: pair_address
    - Position 1: token_address
    - Position 39: dev_wallet_funding (if available)
    - ... (other fields per FIELD_MAP)

    ## Processing Logic
    Only updates pairs that already exist in pair_state. This is useful
    for receiving funding information for pairs we're already tracking.
    """
    try:
        msg_type, pair_data = message

        # Extract pair_address from position 0
        if not pair_data or len(pair_data) < 1:
            logger.warning("Empty pair data in type 2 message")
            return

        pair_address = pair_data[0]

        # Only process if pair is already in memory state
        pair_item = pair_state.get(pair_address)
        if pair_item is None:
            # Pair not tracked yet, ignore this message
            return

        # Extract dev_wallet_funding from position 39 if available
        if len(pair_data) > 39 and pair_data[39] is not None:
            funding_data = pair_data[39]

            # Validate and update funding information
            try:
                dev_wallet_funding = DevWalletFunding.model_validate(funding_data)

                # Convert to dict, update, and validate
                data = pair_item.model_dump()
                data["dev_wallet_funding"] = dev_wallet_funding
                pair_state[pair_address] = PairItem.model_validate(data)

                logger.info(
                    f"Updated funding for tracked pair {pair_address}: "
                    f"{dev_wallet_funding.amount_sol} SOL"
                )
            except Exception as e:
                logger.error(f"Failed to validate funding data for {pair_address}: {e}")

    except Exception as e:
        logger.error(f"Failed to process new pairs message: {e}", exc_info=True)


async def dispatch_pulse_message(message: List[Any]) -> None:
    """
    Route pulse messages to appropriate handlers based on message type.

    ## Parameters
    - `message`: WebSocket pulse message (first element is type)

    ## Message Types
    - `0`: System/heartbeat messages (currently ignored)
    - `1`: Incremental pair updates (most common)
    - `2`: Bulk new pair data
    - `3`: Unknown/reserved (currently ignored)

    ## Error Handling
    Catches routing errors to prevent message dispatcher from crashing.
    Individual handlers have their own error handling as well.
    """
    try:
        msg_type = message[0]

        if msg_type == 0:
            pass  # System messages - no action needed
        elif msg_type == 1:
            await process_pair_update_message(message)
        elif msg_type == 2:
            await process_new_pairs_message(message)
        elif msg_type == 3:
            pass  # Reserved type - no action yet
        else:
            logger.warning(f"Unknown pulse message type: {msg_type}")

    except Exception as e:
        logger.error(f"Failed to dispatch pulse message: {e}", exc_info=True)
