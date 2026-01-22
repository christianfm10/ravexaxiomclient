"""
SQLAlchemy models for storing pair and funding data.
"""

from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    DateTime,
    Date,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

Base = declarative_base()


class WalletAddressDB(Base):
    """Wallet address information with aggregated token metrics."""

    __tablename__ = "wallet_addresses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String, nullable=False, unique=True, index=True)
    migrated_tokens: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    dev_tokens: Mapped[int] = mapped_column(Integer, nullable=True, default=0)

    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    deployed_pairs = relationship(
        "PairDB", foreign_keys="[PairDB.deployer_address_id]", back_populates="deployer"
    )
    # Una wallet solo puede tener UN funding (su primera transacción SOL)
    received_funding = relationship(
        "DevWalletFundingDB",
        foreign_keys="[DevWalletFundingDB.wallet_address_id]",
        back_populates="wallet",
        uselist=False,  # Solo un funding por wallet
    )
    # Una wallet puede haber enviado funding a MUCHAS wallets
    sent_fundings = relationship(
        "DevWalletFundingDB",
        foreign_keys="[DevWalletFundingDB.funding_wallet_address_id]",
        back_populates="funding_wallet",
    )

    def __repr__(self):
        return f"<WalletAddressDB(address={self.address}, migrated={self.migrated_tokens}, dev={self.dev_tokens})>"


class DevWalletFundingDB(Base):
    """Dev wallet funding information."""

    __tablename__ = "dev_wallet_funding"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address_id = Column(
        Integer,
        ForeignKey("wallet_addresses.id"),
        nullable=False,
        unique=True,
        index=True,
    )  # Unique - una wallet solo puede tener un funding
    funding_wallet_address_id = Column(
        Integer, ForeignKey("wallet_addresses.id"), nullable=False, index=True
    )
    signature = Column(
        String, nullable=False, unique=True, index=True
    )  # Unique - signature único
    amount_sol = Column(Float, nullable=False)
    funded_at = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationships to wallet addresses
    wallet = relationship(
        "WalletAddressDB",
        foreign_keys=[wallet_address_id],
        back_populates="received_funding",
    )
    funding_wallet = relationship(
        "WalletAddressDB",
        foreign_keys=[funding_wallet_address_id],
        back_populates="sent_fundings",
    )

    __table_args__ = (
        Index("idx_wallet_address_id", "wallet_address_id"),
        Index("idx_funding_wallet_address_id", "funding_wallet_address_id"),
    )

    def __repr__(self):
        return (
            f"<DevWalletFundingDB(id={self.id}, "
            f"signature={self.signature[:8]}..., amount={self.amount_sol} SOL)>"
        )


class PairDB(Base):
    """Token pair information."""

    __tablename__ = "pairs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pair_address = Column(String, nullable=False, unique=True, index=True)
    signature = Column(String, nullable=True)
    token_address = Column(String, nullable=True, index=True)
    deployer_address_id = Column(
        Integer, ForeignKey("wallet_addresses.id"), nullable=True
    )
    token_name = Column(String, nullable=True)
    token_ticker = Column(String, nullable=True)
    token_uri = Column(String, nullable=True)
    token_decimals = Column(Integer, nullable=True)
    protocol = Column(String, nullable=True, index=True)
    first_market_cap_sol: Mapped[float] = mapped_column(Float, nullable=True)
    high_market_cap_sol: Mapped[float] = mapped_column(Float, nullable=True)
    market_cap_sol = Column(Float, nullable=True)
    initial_liquidity_sol = Column(Float, nullable=True)
    initial_liquidity_token = Column(Float, nullable=True)
    supply = Column(Float, nullable=True)
    bonding_curve_percent = Column(Float, nullable=True)
    created_at = Column(String, nullable=True)
    num_txn = Column(Integer, nullable=True)
    num_buys = Column(Integer, nullable=True)
    num_sells = Column(Integer, nullable=True)
    ath_usd_market_cap = Column(Float, nullable=True)
    ath_market_cap_timestamp = Column(Integer, nullable=True)
    # New field json_data to store additional JSON information
    uri_data = Column(JSONB, nullable=True)
    uri_size = Column(Integer, nullable=True)

    # Timestamps
    inserted_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    deployer = relationship(
        "WalletAddressDB",
        foreign_keys=[deployer_address_id],
        back_populates="deployed_pairs",
    )

    __table_args__ = (
        Index("idx_deployer_address_id", "deployer_address_id"),
        Index("idx_market_cap", "market_cap_sol"),
    )

    def __repr__(self):
        return (
            f"<PairDB(pair_address={self.pair_address}, "
            f"token_name={self.token_name}, protocol={self.protocol})>"
        )


class SolanaPriceDB(Base):
    """Solana price information by date."""

    __tablename__ = "solana_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True, index=True)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_date", "date"),)

    def __repr__(self):
        return f"<SolanaPriceDB(date={self.date}, price_usd={self.price_usd})>"
