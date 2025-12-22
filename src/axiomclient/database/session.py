"""
Database session management and operations.
"""

import logging
from contextlib import contextmanager
from typing import Optional, List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, QueuePool


from axiomclient.database.models import Base, PairDB, DevWalletFundingDB
from axiomclient.models import PairItem

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and operations."""

    def __init__(
        self,
        database_url: str = "sqlite:///axiom_pairs.db",
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
    ):
        """
        Initialize database manager.

        Args:
            database_url: Database connection URL
            echo: Whether to log SQL queries
            pool_size: Connection pool size
            max_overflow: Maximum overflow connections
        """
        self.database_url = database_url

        # Configure engine based on database type
        if database_url.startswith("sqlite"):
            # SQLite doesn't support connection pooling well
            self.engine = create_engine(
                database_url,
                echo=echo,
                poolclass=NullPool,
                connect_args={"check_same_thread": False},
            )
        else:
            # PostgreSQL, MySQL, etc.
            self.engine = create_engine(
                database_url,
                echo=echo,
                poolclass=QueuePool,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_pre_ping=True,  # Verify connections before using
            )

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

        logger.info(f"Database manager initialized with URL: {database_url}")

    def create_tables(self):
        """Create all tables in the database."""
        Base.metadata.create_all(bind=self.engine)
        logger.info("Database tables created successfully")

    def drop_tables(self):
        """Drop all tables in the database."""
        Base.metadata.drop_all(bind=self.engine)
        logger.info("Database tables dropped successfully")

    @contextmanager
    def get_session(self) -> Session:
        """
        Get a database session with automatic cleanup.

        Yields:
            SQLAlchemy session
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def save_pair_batch(self, pair_items: List[PairItem]) -> int:
        """
        Save a batch of pair items to the database.

        Args:
            pair_items: List of PairItem objects to save

        Returns:
            Number of items successfully saved
        """
        if not pair_items:
            return 0

        saved_count = 0

        with self.get_session() as session:
            for pair_item in pair_items:
                try:
                    # Check if pair already exists
                    existing_pair = (
                        session.query(PairDB)
                        .filter_by(pair_address=pair_item.pair_address)
                        .first()
                    )

                    if existing_pair:
                        # Update existing pair
                        self._update_pair_from_item(existing_pair, pair_item)
                        logger.debug(f"Updated existing pair: {pair_item.pair_address}")
                    else:
                        # Create new pair
                        pair_db = self._create_pair_from_item(pair_item)
                        session.add(pair_db)
                        logger.debug(f"Created new pair: {pair_item.pair_address}")

                    saved_count += 1

                except Exception as e:
                    logger.error(
                        f"Error saving pair {pair_item.pair_address}: {e}",
                        exc_info=True,
                    )
                    continue

            # Commit all changes at once
            session.commit()

        logger.info(f"Saved {saved_count}/{len(pair_items)} pairs to database")
        return saved_count

    def _create_pair_from_item(self, pair_item: PairItem) -> PairDB:
        """
        Create a PairDB instance from a PairItem.

        Args:
            pair_item: PairItem to convert

        Returns:
            PairDB instance
        """
        pair_db = PairDB(
            pair_address=pair_item.pair_address,
            signature=pair_item.signature,
            token_address=pair_item.token_address,
            deployer_address=pair_item.deployer_address,
            token_name=pair_item.token_name,
            token_ticker=pair_item.token_ticker,
            token_uri=pair_item.token_uri,
            token_decimals=pair_item.token_decimals,
            protocol=pair_item.protocol,
            market_cap_sol=pair_item.market_cap_sol,
            initial_liquidity_sol=pair_item.initial_liquidity_sol,
            initial_liquidity_token=pair_item.initial_liquidity_token,
            supply=pair_item.supply,
            bonding_curve_percent=pair_item.bonding_curve_percent,
            migrated_tokens=pair_item.migrated_tokens
            if pair_item.migrated_tokens is not None
            else 0,
            created_at=pair_item.created_at,
            dev_tokens=pair_item.dev_tokens if pair_item.dev_tokens is not None else 1,
            num_txn=pair_item.num_txn,
            num_buys=pair_item.num_buys,
            num_sells=pair_item.num_sells,
        )

        # Add dev wallet funding if present
        if pair_item.dev_wallet_funding:
            funding_db = DevWalletFundingDB(
                wallet_address=pair_item.dev_wallet_funding.wallet_address,
                funding_wallet_address=pair_item.dev_wallet_funding.funding_wallet_address,
                signature=pair_item.dev_wallet_funding.signature,
                amount_sol=pair_item.dev_wallet_funding.amount_sol,
                funded_at=pair_item.dev_wallet_funding.funded_at,
                pair_address=pair_item.pair_address,
            )
            pair_db.dev_wallet_funding = funding_db

        return pair_db

    def _update_pair_from_item(self, pair_db: PairDB, pair_item: PairItem):
        """
        Update a PairDB instance from a PairItem.

        Args:
            pair_db: PairDB instance to update
            pair_item: PairItem with new data
        """
        # Update fields if they have values
        if pair_item.signature:
            pair_db.signature = pair_item.signature
        if pair_item.token_address:
            pair_db.token_address = pair_item.token_address
        if pair_item.deployer_address:
            pair_db.deployer_address = pair_item.deployer_address
        if pair_item.token_name:
            pair_db.token_name = pair_item.token_name
        if pair_item.token_ticker:
            pair_db.token_ticker = pair_item.token_ticker
        if pair_item.token_uri:
            pair_db.token_uri = pair_item.token_uri
        if pair_item.token_decimals:
            pair_db.token_decimals = pair_item.token_decimals
        if pair_item.protocol:
            pair_db.protocol = pair_item.protocol
        if pair_item.market_cap_sol is not None:
            pair_db.market_cap_sol = pair_item.market_cap_sol
        if pair_item.initial_liquidity_sol is not None:
            pair_db.initial_liquidity_sol = pair_item.initial_liquidity_sol
        if pair_item.initial_liquidity_token is not None:
            pair_db.initial_liquidity_token = pair_item.initial_liquidity_token
        if pair_item.supply is not None:
            pair_db.supply = pair_item.supply
        if pair_item.bonding_curve_percent is not None:
            pair_db.bonding_curve_percent = pair_item.bonding_curve_percent
        if pair_item.migrated_tokens is not None:
            pair_db.migrated_tokens = pair_item.migrated_tokens
        if pair_item.created_at:
            pair_db.created_at = pair_item.created_at
        if pair_item.dev_tokens is not None:
            pair_db.dev_tokens = pair_item.dev_tokens
        if pair_item.num_txn is not None:
            pair_db.num_txn = pair_item.num_txn
        if pair_item.num_buys is not None:
            pair_db.num_buys = pair_item.num_buys
        if pair_item.num_sells is not None:
            pair_db.num_sells = pair_item.num_sells

        # Update or add dev wallet funding
        if pair_item.dev_wallet_funding:
            if pair_db.dev_wallet_funding:
                # Update existing funding
                funding_db = pair_db.dev_wallet_funding
                funding_db.wallet_address = pair_item.dev_wallet_funding.wallet_address
                funding_db.funding_wallet_address = (
                    pair_item.dev_wallet_funding.funding_wallet_address
                )
                funding_db.signature = pair_item.dev_wallet_funding.signature
                funding_db.amount_sol = pair_item.dev_wallet_funding.amount_sol
                funding_db.funded_at = pair_item.dev_wallet_funding.funded_at
            else:
                # Create new funding
                funding_db = DevWalletFundingDB(
                    wallet_address=pair_item.dev_wallet_funding.wallet_address,
                    funding_wallet_address=pair_item.dev_wallet_funding.funding_wallet_address,
                    signature=pair_item.dev_wallet_funding.signature,
                    amount_sol=pair_item.dev_wallet_funding.amount_sol,
                    funded_at=pair_item.dev_wallet_funding.funded_at,
                    pair_address=pair_item.pair_address,
                )
                pair_db.dev_wallet_funding = funding_db

    def get_pair_by_address(self, pair_address: str) -> Optional[PairDB]:
        """
        Get a pair by its address.

        Args:
            pair_address: Pair address to search for

        Returns:
            PairDB instance or None if not found
        """
        with self.get_session() as session:
            return session.query(PairDB).filter_by(pair_address=pair_address).first()

    def get_pairs_by_protocol(self, protocol: str) -> List[PairDB]:
        """
        Get all pairs for a specific protocol.

        Args:
            protocol: Protocol name

        Returns:
            List of PairDB instances
        """
        with self.get_session() as session:
            return session.query(PairDB).filter_by(protocol=protocol).all()

    def get_pairs_with_funding(self) -> List[PairDB]:
        """
        Get all pairs that have dev wallet funding.

        Returns:
            List of PairDB instances
        """
        with self.get_session() as session:
            return session.query(PairDB).join(DevWalletFundingDB).all()

    def get_statistics(self) -> dict:
        """
        Get database statistics.

        Returns:
            Dictionary with statistics
        """
        with self.get_session() as session:
            total_pairs = session.query(PairDB).count()
            pairs_with_funding = session.query(PairDB).join(DevWalletFundingDB).count()
            total_funding_records = session.query(DevWalletFundingDB).count()

            return {
                "total_pairs": total_pairs,
                "pairs_with_funding": pairs_with_funding,
                "total_funding_records": total_funding_records,
                "funding_percentage": (
                    (pairs_with_funding / total_pairs * 100) if total_pairs > 0 else 0
                ),
            }


# Singleton instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager(
    database_url: str = "sqlite:///axiom_pairs.db",
    echo: bool = False,
) -> DatabaseManager:
    """
    Get or create the database manager singleton.

    Args:
        database_url: Database connection URL
        echo: Whether to log SQL queries

    Returns:
        DatabaseManager instance
    """
    global _db_manager

    if _db_manager is None:
        _db_manager = DatabaseManager(database_url=database_url, echo=echo)
        _db_manager.create_tables()

    return _db_manager
