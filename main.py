"""
Main entry point for the Axiom Monitor application.

Real-time monitoring service for Axiom Trade WebSocket streams.
Tracks token pairs, market caps, funding events, and persists data to database.
"""

import asyncio
import os

from dotenv import load_dotenv
from axiomclient.database import get_async_db_manager
from axiomclient.monitor.logging_config import setup_logging
from axiomclient.monitor.memory_manager import cleanup_and_persist_old_pairs
from axiomclient.monitor.websocket_manager import run_websocket_monitoring

# Load environment variables
load_dotenv()

# Setup logging
logger = setup_logging()


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
            tg.create_task(run_websocket_monitoring())

    except* Exception as e:
        logger.error(f"Fatal error in main loop: {e}", exc_info=True)
        raise
    finally:
        logger.info("=== Axiom Monitor Stopped ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("✅ Shutdown completed gracefully")
