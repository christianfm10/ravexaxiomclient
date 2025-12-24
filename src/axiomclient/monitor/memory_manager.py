"""
Memory management and cleanup operations for the Axiom Monitor.
"""

import asyncio
import logging
from typing import List

from axiomclient.database import AsyncDatabaseManager
from axiomclient.models import PairItem
from axiomclient.monitor.config import (
    CLEANUP_INTERVAL_SECONDS,
    BATCH_SIZE,
    MAX_DEV_TOKENS_THRESHOLD,
    PAIR_AGE_THRESHOLD_SECONDS,
)
from axiomclient.monitor.state import pair_state
from axiomclient.monitor.utils import is_timestamp_older_than
from axiomclient.monitor.database_ops import persist_pairs_to_database

logger = logging.getLogger(__name__)


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
        pair_item.created_at, seconds=PAIR_AGE_THRESHOLD_SECONDS
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
