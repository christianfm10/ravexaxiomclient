"""
SQLAlchemy models for storing pair and funding data.
"""

from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    DateTime,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import JSONB

Base = declarative_base()


class DevWalletFundingDB(Base):
    """Dev wallet funding information."""

    __tablename__ = "dev_wallet_funding"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, nullable=False)
    funding_wallet_address = Column(String, nullable=False)
    signature = Column(
        String, nullable=False, unique=True, index=True
    )  # Unique - signature único
    amount_sol = Column(Float, nullable=False)
    funded_at = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationship back to pair
    pair_address = Column(String, ForeignKey("pairs.pair_address"), nullable=False)

    __table_args__ = (
        Index("idx_wallet_address", "wallet_address"),
        Index("idx_funding_wallet_address", "funding_wallet_address"),
        Index("idx_pair_signature", "pair_address", "signature"),  # Índice compuesto
    )


class PairDB(Base):
    """Token pair information."""

    __tablename__ = "pairs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pair_address = Column(String, nullable=False, unique=True, index=True)
    signature = Column(String, nullable=True)
    token_address = Column(String, nullable=True, index=True)
    deployer_address = Column(String, nullable=True, index=True)
    token_name = Column(String, nullable=True)
    token_ticker = Column(String, nullable=True)
    token_uri = Column(String, nullable=True)
    token_decimals = Column(Integer, nullable=True)
    protocol = Column(String, nullable=True, index=True)
    first_market_cap_sol = Column(Float, nullable=True)
    high_market_cap_sol = Column(Float, nullable=True)
    market_cap_sol = Column(Float, nullable=True)
    initial_liquidity_sol = Column(Float, nullable=True)
    initial_liquidity_token = Column(Float, nullable=True)
    supply = Column(Float, nullable=True)
    bonding_curve_percent = Column(Float, nullable=True)
    migrated_tokens = Column(Integer, nullable=True)
    created_at = Column(String, nullable=True)
    dev_tokens = Column(Integer, nullable=True)
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
    dev_wallet_funding = relationship(
        "DevWalletFundingDB",
        backref="pair",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_protocol_created_at", "protocol", "created_at"),
        Index("idx_market_cap", "market_cap_sol"),
    )

    def __repr__(self):
        return (
            f"<PairDB(pair_address={self.pair_address}, "
            f"token_name={self.token_name}, protocol={self.protocol})>"
        )
