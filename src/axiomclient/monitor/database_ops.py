"""
Database operations for the Axiom Monitor.
"""

import logging
from typing import List

from axiomclient.database import AsyncDatabaseManager
from axiomclient.models import PairItem

logger = logging.getLogger(__name__)


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
