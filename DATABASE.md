# Database Documentation

## Overview

The Axiom Client now includes a complete SQLAlchemy integration for persisting pair and funding data.

## Features

- ✅ **SQLAlchemy ORM** - Modern Python database toolkit
- ✅ **Multiple Database Support** - SQLite, PostgreSQL, MySQL
- ✅ **Automatic Schema Creation** - Tables created on first run
- ✅ **Batch Operations** - Efficient bulk inserts
- ✅ **Relationship Management** - Pairs with dev wallet funding
- ✅ **Query Helpers** - Pre-built common queries
- ✅ **Statistics** - Database metrics and analytics
- ✅ **CSV Export** - Export data for analysis

## Database Structure

### Tables

#### `pairs`
Stores token pair information:
- `pair_address` (unique) - Pair identifier
- `token_name`, `token_ticker` - Token details
- `protocol` - Protocol name (e.g., "pump v1")
- `market_cap_sol`, `initial_liquidity_sol` - Financial data
- `created_at` - Pair creation timestamp
- Timestamps: `inserted_at`, `updated_at`

#### `dev_wallet_funding`
Stores dev wallet funding information:
- `wallet_address` - Developer wallet
- `funding_wallet_address` - Source of funding
- `amount_sol` - Funding amount
- `signature` (unique) - Transaction signature
- `pair_address` (FK) - Related pair

## Installation

```bash
# Install dependencies
uv sync
# or
pip install sqlalchemy aiogram
```

## Configuration

### Environment Variables

Create a `.env` file (see `.env.example`):

```bash
# SQLite (default, no setup needed)
DATABASE_URL=sqlite:///axiom_pairs.db

# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/axiom_db

# MySQL
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/axiom_db

# SQL logging (optional)
ECHO_SQL=false
```

## Usage

### Running the Monitor

```bash
# Run with default SQLite database
python src/axiomclient/monitor.py

# Run with custom database
DATABASE_URL=postgresql://user:pass@localhost/axiom python src/axiomclient/monitor.py
```

### Querying the Database

```python
from axiomclient.database import get_db_manager

# Initialize database manager
db_manager = get_db_manager(database_url="sqlite:///axiom_pairs.db")

# Get statistics
stats = db_manager.get_statistics()
print(f"Total pairs: {stats['total_pairs']}")
print(f"Pairs with funding: {stats['pairs_with_funding']}")

# Get pairs with dev wallet funding
pairs_with_funding = db_manager.get_pairs_with_funding()
for pair in pairs_with_funding:
    print(f"{pair.token_name}: {pair.dev_wallet_funding.amount_sol} SOL")

# Get pairs by protocol
pump_pairs = db_manager.get_pairs_by_protocol("pump v1")
print(f"Found {len(pump_pairs)} pump v1 pairs")

# Get specific pair
pair = db_manager.get_pair_by_address("0x123...")
if pair:
    print(f"Token: {pair.token_name} ({pair.token_ticker})")
```

### Advanced Queries

```python
from axiomclient.database import get_db_manager
from axiomclient.database.models import PairDB, DevWalletFundingDB
from sqlalchemy import func, desc

db_manager = get_db_manager()

with db_manager.get_session() as session:
    # Top pairs by market cap
    top_pairs = (
        session.query(PairDB)
        .filter(PairDB.market_cap_sol.isnot(None))
        .order_by(desc(PairDB.market_cap_sol))
        .limit(10)
        .all()
    )
    
    # Average funding amount
    avg_funding = (
        session.query(func.avg(DevWalletFundingDB.amount_sol))
        .scalar()
    )
    
    # Pairs with high liquidity
    high_liquidity = (
        session.query(PairDB)
        .filter(PairDB.initial_liquidity_sol > 5.0)
        .all()
    )
```

### Example Queries Script

```bash
# Run the example queries
python examples/database_query_example.py
```

This script demonstrates:
- Basic statistics
- Filtering by protocol
- Querying with funding
- Top pairs by market cap
- Aggregations and grouping
- CSV export

## Database Manager API

### Initialization

```python
from axiomclient.database import get_db_manager

# Get singleton instance (SQLite)
db_manager = get_db_manager()

# Custom database
db_manager = get_db_manager(
    database_url="postgresql://localhost/axiom",
    echo=True  # Log SQL queries
)
```

### Methods

#### `create_tables()`
Create all database tables.

#### `save_pair_batch(pair_items: List[PairItem]) -> int`
Save multiple pairs in one transaction.

```python
from axiomclient.models import PairItem

pairs = [PairItem(...), PairItem(...)]
saved_count = db_manager.save_pair_batch(pairs)
```

#### `get_pair_by_address(pair_address: str) -> Optional[PairDB]`
Get a specific pair by address.

#### `get_pairs_by_protocol(protocol: str) -> List[PairDB]`
Get all pairs for a protocol.

#### `get_pairs_with_funding() -> List[PairDB]`
Get pairs that have dev wallet funding.

#### `get_statistics() -> dict`
Get database statistics.

Returns:
```python
{
    "total_pairs": 1234,
    "pairs_with_funding": 567,
    "total_funding_records": 567,
    "funding_percentage": 45.9
}
```

#### `get_session()`
Get a context-managed database session.

```python
with db_manager.get_session() as session:
    # Your queries here
    results = session.query(PairDB).all()
    # Auto-commits on success, auto-rolls back on error
```

## Monitor Improvements

The `monitor.py` script now includes:

### 1. **Structured Logging**
```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
```

### 2. **Database Integration**
- Automatic database initialization on startup
- Batch saving of pairs with funding
- Periodic statistics logging

### 3. **Better Error Handling**
- Try-catch blocks around all operations
- Detailed error logging with stack traces
- Graceful degradation

### 4. **Memory Management**
- Configurable batch sizes
- Periodic cleanup of old pairs
- Controlled memory footprint

### 5. **Type Hints**
- Full type annotations
- Better IDE support
- Clearer code

### 6. **Configuration**
- Environment variable support
- Configurable database URL
- SQL query logging option

## PostgreSQL Setup (Optional)

If you want to use PostgreSQL instead of SQLite:

```bash
# Install PostgreSQL adapter
pip install psycopg2-binary

# Create database
createdb axiom_db

# Set environment variable
export DATABASE_URL=postgresql://user:password@localhost/axiom_db

# Run monitor
python src/axiomclient/monitor.py
```

## MySQL Setup (Optional)

```bash
# Install MySQL adapter
pip install pymysql

# Create database
mysql -u root -p -e "CREATE DATABASE axiom_db;"

# Set environment variable
export DATABASE_URL=mysql+pymysql://user:password@localhost/axiom_db

# Run monitor
python src/axiomclient/monitor.py
```

## Performance Tips

### SQLite
- Good for development and moderate loads
- Single file, no setup required
- Concurrent reads, single writer

### PostgreSQL
- Best for production
- Excellent concurrent performance
- Advanced query features
- Recommended for high-volume monitoring

### Indexes
The schema includes indexes on:
- `pair_address` (unique)
- `token_address`
- `deployer_address`
- `protocol`
- `signature` (unique on funding)

## Backup

### SQLite
```bash
# Backup database file
cp axiom_pairs.db axiom_pairs_backup.db

# Or export to SQL
sqlite3 axiom_pairs.db .dump > backup.sql
```

### PostgreSQL
```bash
pg_dump axiom_db > backup.sql
```

## Troubleshooting

### "Table already exists" error
```python
# Drop and recreate tables
db_manager.drop_tables()
db_manager.create_tables()
```

### Import errors
```bash
# Make sure SQLAlchemy is installed
pip install sqlalchemy

# For PostgreSQL
pip install psycopg2-binary

# For MySQL
pip install pymysql
```

### Performance issues
- Enable connection pooling (automatic for PostgreSQL/MySQL)
- Add more indexes if needed
- Consider upgrading to PostgreSQL

## Export Data

```python
# Run the example script
python examples/database_query_example.py

# This creates CSV files:
# - pairs_export_YYYYMMDD_HHMMSS.csv
# - funding_export_YYYYMMDD_HHMMSS.csv
```

## Schema Migration

For future schema changes, consider using Alembic:

```bash
pip install alembic
alembic init migrations
# Edit alembic.ini and env.py
alembic revision --autogenerate -m "Description"
alembic upgrade head
```
