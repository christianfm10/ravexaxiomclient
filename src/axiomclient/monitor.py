"""
# Axiom Monitor Application

Real-time monitoring service for Axiom Trade WebSocket streams.
Tracks token pairs, market caps, funding events, and persists data to database.

## Key Features
- Real-time WebSocket connection to Axiom Trade
- In-memory state management for active pairs
- Automatic database persistence with batching
- Market cap tracking (first, highest, latest)
- Memory cleanup for old/inactive pairs
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from axiomclient.auth.auth_manager import AuthManager
from axiomclient.websocket._client import AxiomWebSocketClient
from axiomclient.models import DevWalletFunding, PairItem
from axiomclient.database import get_async_db_manager, AsyncDatabaseManager

# Load environment variables
load_dotenv()

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

# Create consistent formatter for all handlers
log_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Console handler: Show all INFO+ messages
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(log_formatter)

# File handler: Only WARNING and ERROR, with rotation to prevent disk overflow
file_handler = RotatingFileHandler(
    "axiom_monitor.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,  # Keep 5 backup files
)
file_handler.setLevel(logging.WARNING)
file_handler.setFormatter(log_formatter)

# Configure root logger with both handlers
logging.basicConfig(
    level=logging.INFO,
    handlers=[console_handler, file_handler],
)
logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL STATE AND CONSTANTS
# ============================================================================

# In-memory cache of active token pairs
# Design decision: Store in memory for fast access, persist to DB periodically
pair_state: Dict[str, PairItem] = {}

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
CLEANUP_INTERVAL_SECONDS = 30
BATCH_SIZE = 1000
MAX_DEV_TOKENS_THRESHOLD = 20  # Pairs with more dev tokens are ignored
PAIR_AGE_THRESHOLD_MINUTES = 5  # How old before persisting to DB


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def is_timestamp_older_than(
    timestamp: str, seconds: Optional[int] = None, minutes: Optional[int] = None
) -> bool:
    """
    Check if a timestamp is older than a specified duration.

    ## Parameters
    - `timestamp`: ISO format timestamp string (e.g., "2024-12-22T10:30:00Z")
    - `seconds`: Number of seconds to check against (optional)
    - `minutes`: Number of minutes to check against (optional)

    ## Returns
    - `True` if timestamp is older than specified duration
    - `False` otherwise or if parsing fails

    ## Design Notes
    Gracefully handles malformed timestamps by logging and returning False.
    This prevents one bad timestamp from crashing the entire application.
    """
    if seconds is None and minutes is None:
        raise ValueError("Must specify either seconds or minutes")

    try:
        # Parse ISO format with timezone handling
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        if seconds is not None:
            return now - dt > timedelta(seconds=seconds)
        else:
            return now - dt > timedelta(minutes=minutes)

    except (ValueError, AttributeError) as e:
        logger.error(f"Failed to parse timestamp '{timestamp}': {e}")
        return False


# ============================================================================
# DATABASE OPERATIONS
# ============================================================================


async def persist_pairs_to_database(
    pairs: List[PairItem], db_manager: AsyncDatabaseManager
) -> None:
    """
    Persist a batch of token pairs to the database.

    ## Parameters
    - `pairs`: List of PairItem objects to save
    - `db_manager`: Database manager instance

    ## Design Notes
    Uses batch operations for efficiency. Logs statistics after each save
    to provide visibility into database state and operation success.
    """
    if not pairs:
        logger.info("No pairs to persist")
        return

    try:
        saved_count = await db_manager.save_pair_batch(pairs)
        logger.info(f"Persisted {saved_count}/{len(pairs)} pairs to database")

        # Log current database statistics for monitoring
        stats = await db_manager.get_statistics()
        logger.info(
            f"Database: {stats['total_pairs']} total pairs, "
            f"{stats['pairs_with_funding']} with funding "
            f"({stats['funding_percentage']:.1f}%)"
        )
    except Exception as e:
        logger.error(f"Failed to persist batch to database: {e}", exc_info=True)


# ============================================================================
# MEMORY MANAGEMENT
# ============================================================================


def should_skip_pair(pair_item: PairItem) -> bool:
    """
    Determine if a pair should be skipped/removed from tracking.

    ## Parameters
    - `pair_item`: The pair to evaluate

    ## Returns
    - `True` if pair should be skipped (missing data or too many dev tokens)
    - `False` if pair should continue being tracked

    ## Design Notes
    Filters out pairs with excessive dev tokens (potential rug pulls) and
    pairs with missing creation timestamps.
    """
    # Skip if no creation timestamp
    if pair_item.created_at is None:
        return True

    # Skip if dev tokens exceed threshold (potential manipulation)
    if pair_item.dev_tokens and pair_item.dev_tokens > MAX_DEV_TOKENS_THRESHOLD:
        return True

    return False


def is_pair_ready_for_persistence(pair_item: PairItem) -> bool:
    """
    Check if a pair is old enough to be persisted to database.

    ## Parameters
    - `pair_item`: The pair to evaluate

    ## Returns
    - `True` if pair should be persisted
    - `False` if pair should remain in memory

    ## Design Notes
    We keep recent pairs in memory for fast updates, only persisting
    after they reach a certain age to reduce database write operations.
    """
    if pair_item.created_at is None:
        return False

    return is_timestamp_older_than(
        pair_item.created_at, minutes=PAIR_AGE_THRESHOLD_MINUTES
    )


async def cleanup_and_persist_old_pairs(db_manager: AsyncDatabaseManager) -> None:
    """
    Background task that periodically cleans memory and persists old pairs.

    ## Parameters
    - `db_manager`: Database manager for persistence operations

    ## Algorithm
    1. Sleep for configured interval
    2. Iterate through all pairs in memory
    3. Skip pairs that should be filtered out
    4. Collect pairs old enough for persistence
    5. Batch persist to database
    6. Remove persisted pairs from memory

    ## Design Notes
    Runs continuously in background. Uses batching to minimize database
    operations. Keeps memory footprint bounded by removing old entries.
    """
    logger.info("Memory cleanup task started")

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

            pairs_to_persist: List[PairItem] = []
            pairs_to_remove: List[str] = []

            # Evaluate each pair in memory
            for pair_address, pair_item in list(pair_state.items()):
                # Filter out unwanted pairs
                if should_skip_pair(pair_item):
                    pairs_to_remove.append(pair_address)
                    continue

                # Check if pair is ready for persistence
                if is_pair_ready_for_persistence(pair_item):
                    pairs_to_persist.append(pair_item)
                    pairs_to_remove.append(pair_address)

                    # Persist in batches to avoid memory buildup
                    if len(pairs_to_persist) >= BATCH_SIZE:
                        await persist_pairs_to_database(pairs_to_persist, db_manager)
                        pairs_to_persist = []

            # Persist any remaining pairs
            if pairs_to_persist:
                await persist_pairs_to_database(pairs_to_persist, db_manager)

            # Remove persisted pairs from memory
            for pair_address in pairs_to_remove:
                del pair_state[pair_address]

            if pairs_to_remove:
                logger.info(
                    f"Cleaned {len(pairs_to_remove)} pairs from memory. "
                    f"Active pairs: {len(pair_state)}"
                )

        except Exception as e:
            logger.error(f"Error in cleanup task: {e}", exc_info=True)
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


# ============================================================================
# WEBSOCKET MESSAGE HANDLERS
# ============================================================================


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
    ## Filtering Rules
    1. Only accept "Pump V1" protocol tokens
    2. Reject Mayhem tokens (high risk/volatility)
    """
    protocol = content.get("protocol", "").lower()
    if protocol != "pump v1":
        return False

    # Check for Mayhem flag
    protocol_details = content.get("protocol_details", {})
    if protocol_details and protocol_details.get("isMayhem", False):
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

        # Create pair item from message
        pair_item = PairItem(**content)

        # Initialize market cap tracking
        initialize_market_cap_fields(pair_item)

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
            # Log if we're missing funding for a pair
            for field_index, _ in changes:
                if field_index == 39:  # dev_wallet_funding
                    logger.info(f"Funding update for untracked pair: {pair_address}")
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
    - `message`: List containing [msg_type, new_pairs_data]

    ## Design Notes
    Currently logs for monitoring purposes. Full implementation would
    create PairItem objects from the bulk data.
    """
    try:
        msg_type, new_pairs = message
        data = new_pairs[1]
        pair_address = data[0]
        logger.info(f"Received bulk pair message (type {msg_type}): {pair_address}")
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


# ============================================================================
# WEBSOCKET CLIENT MANAGEMENT
# ============================================================================


async def run_websocket_monitoring(db_manager: AsyncDatabaseManager) -> None:
    """
    Initialize and run WebSocket client for monitoring Axiom Trade streams.

    ## Parameters
    - `db_manager`: Database manager for persistence operations

    ## Setup Steps
    1. Authenticate with Axiom (reuse saved tokens if available)
    2. Create WebSocket client
    3. Establish main connection
    4. Subscribe to new tokens and pulse streams
    5. Start message handler tasks

    ## Task Management
    Uses TaskGroup to manage multiple concurrent tasks:
    - Main WebSocket message handler
    - Pulse WebSocket message handler (if connection exists)

    TaskGroup ensures proper error propagation and graceful shutdown.

    ## Error Handling
    Keyboard interrupts are caught and logged for clean shutdown.
    Other exceptions propagate up to main() for handling.
    """
    # Authenticate with Axiom Trade
    auth_manager = AuthManager(use_saved_tokens=True)

    if not auth_manager.is_authenticated():
        logger.info("Authenticating with Axiom Trade...")
        auth_manager.authenticate()
    else:
        logger.info("Using saved authentication tokens")

    logger.info("Authentication successful")

    # Initialize WebSocket client
    ws_client = AxiomWebSocketClient(auth_manager=auth_manager)

    # Connect to main WebSocket
    await ws_client.connect()
    logger.info("Main WebSocket connection established")

    # Subscribe to data streams
    await ws_client.subscribe_new_tokens(handle_new_token_message)
    await ws_client.subscribe_pulse(dispatch_pulse_message)

    logger.info("WebSocket subscriptions configured")
    logger.info("Starting message handlers...")

    # Run message handlers in TaskGroup for proper lifecycle management
    try:
        async with asyncio.TaskGroup() as tg:
            # Main WebSocket message handler
            tg.create_task(ws_client._message_handler())

            # Pulse WebSocket message handler (if connected)
            if ws_client.ws_pulse:
                tg.create_task(ws_client._pulse_message_handler())

    except* KeyboardInterrupt:
        logger.info("WebSocket monitoring interrupted")


# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================


async def main() -> None:
    """
    Main entry point for the Axiom Monitor application.

    ## Initialization
    1. Load database configuration from environment
    2. Initialize async database manager
    3. Log startup statistics

    ## Task Orchestration
    Runs two concurrent background tasks using TaskGroup:
    - Memory cleanup and persistence task
    - WebSocket monitoring task

    TaskGroup provides:
    - Automatic error propagation from any task
    - Clean shutdown of all tasks on error
    - Proper exception group handling

    ## Error Handling
    - KeyboardInterrupt: Graceful shutdown logging
    - Other exceptions: Logged with full traceback
    - Finally block: Ensures shutdown message is logged

    ## Environment Variables
    - `DATABASE_URL`: Database connection string (default: SQLite)
    - `ECHO_SQL`: Enable SQL query logging (default: false)
    """
    logger.info("=== Axiom Monitor Starting ===")

    # Load database configuration
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///axiom_pairs.db")
    echo_sql = os.getenv("ECHO_SQL", "false").lower() == "true"

    logger.info(f"Initializing database: {database_url}")
    db_manager = await get_async_db_manager(database_url=database_url, echo=echo_sql)

    # Log initial database state
    stats = await db_manager.get_statistics()
    logger.info(
        f"Database ready: {stats['total_pairs']} existing pairs, "
        f"{stats['pairs_with_funding']} with funding data"
    )

    logger.info("Starting background tasks...")

    # Run concurrent tasks with proper error handling
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(cleanup_and_persist_old_pairs(db_manager))
            tg.create_task(run_websocket_monitoring(db_manager))

    except* KeyboardInterrupt:
        logger.info("Shutdown signal received")
    except* Exception as e:
        logger.error(f"Fatal error in main loop: {e}", exc_info=True)
    finally:
        logger.info("=== Axiom Monitor Stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
