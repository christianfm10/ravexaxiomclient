"""
Async database session management and operations.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, List

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

from axiomclient.database.models import (
    Base,
    PairDB,
    DevWalletFundingDB,
    WalletAddressDB,
)
from axiomclient.models import DevWalletFunding, PairItem

logger = logging.getLogger(__name__)


class AsyncDatabaseManager:
    """Manages async database connections and operations."""

    def __init__(
        self,
        database_url: str = "sqlite+aiosqlite:///axiom_pairs.db",
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
    ):
        """
        Initialize async database manager.

        Args:
            database_url: Database connection URL (async driver)
            echo: Whether to log SQL queries
            pool_size: Connection pool size
            max_overflow: Maximum overflow connections
        """
        self.database_url = database_url

        # Configure engine based on database type
        if database_url.startswith("sqlite"):
            # SQLite doesn't support connection pooling well
            self.engine = create_async_engine(
                database_url,
                echo=echo,
                poolclass=NullPool,
                connect_args={"check_same_thread": False},
            )
        else:
            # PostgreSQL (asyncpg), MySQL (aiomysql), etc.
            self.engine = create_async_engine(
                database_url,
                echo=echo,
                poolclass=AsyncAdaptedQueuePool,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_pre_ping=True,  # Verify connections before using
            )

        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        logger.info(f"Async database manager initialized with URL: {database_url}")

    async def create_tables(self):
        """Create all tables in the database."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")

    async def drop_tables(self):
        """Drop all tables in the database."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Database tables dropped successfully")

    async def close(self):
        """Close database engine and connections."""
        await self.engine.dispose()
        logger.info("Database connections closed")

    @asynccontextmanager
    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """
        Get an async database session with automatic cleanup.

        Yields:
            AsyncSession instance
        """
        session = self.AsyncSessionLocal()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise
        finally:
            await session.close()

    async def save_pair_batch(self, pair_items: List[PairItem]) -> int:
        """
        Save a batch of pair items to the database asynchronously.

        Args:
            pair_items: List of PairItem objects to save

        Returns:
            Number of items successfully saved
        """
        if not pair_items:
            return 0

        saved_count = 0

        async with self.get_session() as session:
            for pair_item in pair_items:
                try:
                    # Check if pair already exists
                    from sqlalchemy import select

                    result = await session.execute(
                        select(PairDB).filter_by(pair_address=pair_item.pair_address)
                    )
                    existing_pair = result.scalar_one_or_none()

                    if existing_pair:
                        # Update existing pair
                        await self._update_pair_from_item_async(
                            session, existing_pair, pair_item
                        )
                        logger.debug(f"Updated existing pair: {pair_item.pair_address}")
                    else:
                        # Create new pair
                        pair_db = await self._create_pair_from_item_async(
                            session, pair_item
                        )
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
            await session.commit()

        logger.info(f"Saved {saved_count}/{len(pair_items)} pairs to database")
        return saved_count

    async def _get_or_create_wallet(
        self, session: AsyncSession, address: str
    ) -> WalletAddressDB:
        """
        Get existing wallet or create new one.

        Args:
            session: Database session
            address: Wallet address string

        Returns:
            WalletAddressDB instance
        """
        from sqlalchemy import select

        # Check if wallet already exists
        result = await session.execute(
            select(WalletAddressDB).filter_by(address=address)
        )
        wallet = result.scalar_one_or_none()

        if wallet:
            return wallet

        # Create new wallet
        wallet = WalletAddressDB(address=address)
        session.add(wallet)
        await session.flush()  # Flush to get the ID
        return wallet

    async def _create_pair_from_item_async(
        self, session: AsyncSession, pair_item: PairItem
    ) -> PairDB:
        """
        Create a PairDB instance from a PairItem (async version).

        Args:
            session: Database session
            pair_item: PairItem to convert

        Returns:
            PairDB instance
        """
        # Get or create deployer wallet if deployer_address exists
        deployer_wallet_id = None
        deployer_wallet = None
        if pair_item.deployer_address:
            deployer_wallet = await self._get_or_create_wallet(
                session, pair_item.deployer_address
            )
            deployer_wallet_id = deployer_wallet.id

        pair_db = PairDB(
            pair_address=pair_item.pair_address,
            signature=pair_item.signature,
            token_address=pair_item.token_address,
            deployer_address_id=deployer_wallet_id,
            token_name=pair_item.token_name,
            token_ticker=pair_item.token_ticker,
            token_uri=pair_item.token_uri,
            token_decimals=pair_item.token_decimals,
            protocol=pair_item.protocol,
            first_market_cap_sol=pair_item.first_market_cap_sol,
            high_market_cap_sol=pair_item.high_market_cap_sol,
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

        # If deployer wallet exists and pair has funding data, add it to the deployer
        if deployer_wallet and pair_item.dev_wallet_funding:
            await self._add_funding_to_wallet_if_not_exists(
                session, deployer_wallet, pair_item.dev_wallet_funding
            )

        return pair_db

    async def _add_funding_to_wallet_if_not_exists(
        self,
        session: AsyncSession,
        wallet: WalletAddressDB,
        dev_wallet_funding: DevWalletFunding,
    ):
        """
        Add funding to wallet only if signature doesn't already exist in database.

        Args:
            session: Database session
            wallet: WalletAddressDB instance (the deployer)
            pair_item: PairItem with funding data
        """
        from sqlalchemy import select

        # Check if wallet already has funding
        result = await session.execute(
            select(DevWalletFundingDB).filter_by(wallet_address_id=wallet.id)
        )
        existing_funding = result.scalar_one_or_none()

        if existing_funding:
            logger.debug(
                f"Wallet {wallet.address[:8]}... already has funding (id={existing_funding.id})"
            )
            return

        # Get or create funding wallet address
        funding_wallet = await self._get_or_create_wallet(
            session, dev_wallet_funding.funding_wallet_address
        )

        # Create new funding for this wallet
        funding_db = DevWalletFundingDB(
            wallet_address_id=wallet.id,
            funding_wallet_address_id=funding_wallet.id,
            signature=dev_wallet_funding.signature,
            amount_sol=dev_wallet_funding.amount_sol,
            funded_at=dev_wallet_funding.funded_at,
        )
        session.add(funding_db)
        await session.flush()  # Flush to get the funding_db.id

        logger.debug(
            f"Added new funding to wallet {wallet.address[:8]}... (funding_id={funding_db.id}, signature={dev_wallet_funding.signature[:8]}...)"
        )

    async def _update_pair_from_item_async(
        self, session: AsyncSession, pair_db: PairDB, pair_item: PairItem
    ):
        """
        Update a PairDB instance from a PairItem (async version).

        Args:
            session: Database session
            pair_db: PairDB instance to update
            pair_item: PairItem with new data
        """
        # Handle deployer_address separately (requires wallet lookup)
        if pair_item.deployer_address:
            deployer_wallet = await self._get_or_create_wallet(
                session, pair_item.deployer_address
            )
            pair_db.deployer_address_id = deployer_wallet.id

        # Fields that update only if they have truthy values
        simple_fields = [
            "signature",
            "token_address",
            "token_name",
            "token_ticker",
            "token_uri",
            "token_decimals",
            "protocol",
            "created_at",
        ]
        for field in simple_fields:
            value = getattr(pair_item, field, None)
            if value:
                setattr(pair_db, field, value)

        # Fields that update if not None (including 0 or empty values)
        nullable_fields = [
            "initial_liquidity_sol",
            "initial_liquidity_token",
            "supply",
            "bonding_curve_percent",
            "migrated_tokens",
            "dev_tokens",
            "num_txn",
            "num_buys",
            "num_sells",
            "market_cap_sol",
        ]
        for field in nullable_fields:
            value = getattr(pair_item, field, None)
            if value is not None:
                setattr(pair_db, field, value)

        # Special case: first_market_cap_sol - only set once
        if (
            pair_item.first_market_cap_sol is not None
            and pair_db.first_market_cap_sol is None
        ):
            pair_db.first_market_cap_sol = pair_item.first_market_cap_sol

        # Special case: high_market_cap_sol - only update if greater
        if pair_item.high_market_cap_sol is not None:
            if (
                pair_db.high_market_cap_sol is None
                or pair_item.high_market_cap_sol > pair_db.high_market_cap_sol
            ):
                pair_db.high_market_cap_sol = pair_item.high_market_cap_sol

        # Add dev wallet funding to deployer if present
        if pair_item.dev_wallet_funding and pair_db.deployer:
            await self._add_funding_to_wallet_if_not_exists(
                session, pair_db.deployer, pair_item.dev_wallet_funding
            )

    def _update_pair_from_item(self, pair_db: PairDB, pair_item: PairItem):
        """
        Update a PairDB instance from a PairItem.

        Args:
            pair_db: PairDB instance to update
            pair_item: PairItem with new data
        """
        # Fields that update only if they have truthy values
        simple_fields = [
            "signature",
            "token_address",
            "deployer_address",
            "token_name",
            "token_ticker",
            "token_uri",
            "token_decimals",
            "protocol",
            "created_at",
        ]
        for field in simple_fields:
            value = getattr(pair_item, field, None)
            if value:
                setattr(pair_db, field, value)

        # Fields that update if not None (including 0 or empty values)
        nullable_fields = [
            "initial_liquidity_sol",
            "initial_liquidity_token",
            "supply",
            "bonding_curve_percent",
            "migrated_tokens",
            "dev_tokens",
            "num_txn",
            "num_buys",
            "num_sells",
            "market_cap_sol",
        ]
        for field in nullable_fields:
            value = getattr(pair_item, field, None)
            if value is not None:
                setattr(pair_db, field, value)

        # Special case: first_market_cap_sol - only set once
        if (
            pair_item.first_market_cap_sol is not None
            and pair_db.first_market_cap_sol is None
        ):
            pair_db.first_market_cap_sol = pair_item.first_market_cap_sol

        # Special case: high_market_cap_sol - only update if greater
        if pair_item.high_market_cap_sol is not None:
            if (
                pair_db.high_market_cap_sol is None
                or pair_item.high_market_cap_sol > pair_db.high_market_cap_sol
            ):
                pair_db.high_market_cap_sol = pair_item.high_market_cap_sol

    async def get_pair_by_address(self, pair_address: str) -> Optional[PairDB]:
        """
        Get a pair by its address asynchronously.

        Args:
            pair_address: Pair address to search for

        Returns:
            PairDB instance or None if not found
        """
        from sqlalchemy import select

        async with self.get_session() as session:
            result = await session.execute(
                select(PairDB).filter_by(pair_address=pair_address)
            )
            return result.scalar_one_or_none()

    async def get_pairs_by_protocol(self, protocol: str) -> List[PairDB]:
        """
        Get all pairs for a specific protocol asynchronously.

        Args:
            protocol: Protocol name

        Returns:
            List of PairDB instances
        """
        from sqlalchemy import select

        async with self.get_session() as session:
            result = await session.execute(select(PairDB).filter_by(protocol=protocol))
            return list(result.scalars().all())

    async def get_pairs_with_funding(self) -> List[PairDB]:
        """
        Get all pairs that have dev wallet funding asynchronously.

        Returns:
            List of PairDB instances with deployers that have funding
        """
        from sqlalchemy import select

        async with self.get_session() as session:
            # Join: PairDB -> WalletAddressDB -> DevWalletFundingDB
            result = await session.execute(
                select(PairDB)
                .join(WalletAddressDB, PairDB.deployer_address_id == WalletAddressDB.id)
                .join(
                    DevWalletFundingDB,
                    DevWalletFundingDB.wallet_address_id == WalletAddressDB.id,
                )
            )
            return list(result.scalars().all())

    async def get_statistics(self) -> dict:
        """
        Get database statistics asynchronously.

        Returns:
            Dictionary with statistics
        """
        from sqlalchemy import select, func

        async with self.get_session() as session:
            # Total pairs
            total_result = await session.execute(select(func.count(PairDB.id)))
            total_pairs = total_result.scalar() or 0

            # Pairs with funding (via deployer wallet)
            funding_result = await session.execute(
                select(func.count(PairDB.id))
                .join(WalletAddressDB, PairDB.deployer_address_id == WalletAddressDB.id)
                .join(
                    DevWalletFundingDB,
                    DevWalletFundingDB.wallet_address_id == WalletAddressDB.id,
                )
            )
            pairs_with_funding = funding_result.scalar() or 0

            # Total funding records
            funding_records_result = await session.execute(
                select(func.count(DevWalletFundingDB.id))
            )
            total_funding_records = funding_records_result.scalar()

            return {
                "total_pairs": total_pairs,
                "pairs_with_funding": pairs_with_funding,
                "total_funding_records": total_funding_records,
                "funding_percentage": (
                    (pairs_with_funding / total_pairs * 100) if total_pairs > 0 else 0
                ),
            }


# Singleton instance
_async_db_manager: Optional[AsyncDatabaseManager] = None


async def get_async_db_manager(
    database_url: str = "sqlite+aiosqlite:///axiom_pairs.db",
    echo: bool = False,
) -> AsyncDatabaseManager:
    """
    Get or create the async database manager singleton.

    Args:
        database_url: Database connection URL (with async driver)
        echo: Whether to log SQL queries

    Returns:
        AsyncDatabaseManager instance
    """
    global _async_db_manager

    if _async_db_manager is None:
        _async_db_manager = AsyncDatabaseManager(database_url=database_url, echo=echo)
        await _async_db_manager.create_tables()

    return _async_db_manager
