"""
Utility functions for the Axiom Monitor.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


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
        elif minutes is not None:
            return now - dt > timedelta(minutes=minutes)
        return False

    except (ValueError, AttributeError) as e:
        logger.error(f"Failed to parse timestamp '{timestamp}': {e}")
        return False
