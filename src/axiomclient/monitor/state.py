"""
Global state management for the Axiom Monitor.
"""

from typing import Dict
from axiomclient.models import PairItem

# In-memory cache of active token pairs
# Design decision: Store in memory for fast access, persist to DB periodically
pair_state: Dict[str, PairItem] = {}
