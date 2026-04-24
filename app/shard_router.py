"""
Shard Router Module

Implements deterministic hash-based sharding using:

```
shard_id = CRC32(MemberID) % NUM_SHARDS
```

This module centralizes all routing logic for:

* Single-shard queries (member-scoped)
* Fan-out queries (cross-shard aggregation)

Design Goals:

* Deterministic routing (no lookup tables required)
* Even data distribution
* Minimal coordination overhead
"""

from __future__ import annotations

import zlib
from typing import List

# ---------- CONFIG ----------

NUM_SHARDS: int = 3
ALL_SHARDS: List[int] = list(range(NUM_SHARDS))

SHARDED_TABLES = {"member", "post", "comment"}

# ---------- HASHING ----------

def hash_member_id(member_id: int) -> int:
    """
    Compute deterministic hash for a shard key.

    Uses CRC32 for:
    - Fast computation
    - Stable cross-process hashing
    """
    if member_id is None:
        raise ValueError("member_id cannot be None")

    return zlib.crc32(str(member_id).encode("utf-8"))

# ---------- ROUTING ----------

def get_shard_id(member_id: int) -> int:
    """
    Map a MemberID to shard_id.

    Ensures:
    - Deterministic placement
    - Even distribution across shards
    """
    return hash_member_id(member_id) % NUM_SHARDS

def get_shard_table(base_table: str, member_id: int) -> str:
    """
    Resolve physical table name for a logical table.

    Example:
        get_shard_table("post", 42) -> shard_1_post
    """
    base = base_table.lower()

    if base not in SHARDED_TABLES:
        raise ValueError(f"{base_table} is not a sharded table")

    shard_id = get_shard_id(member_id)
    return f"shard_{shard_id}_{base}"

# ---------- FAN-OUT ----------

def all_shard_tables(base_table: str) -> List[str]:
    """
    Return all shard table names for fan-out queries.

    Used for:
    - Global feeds
    - Search queries
    """
    base = base_table.lower()

    if base not in SHARDED_TABLES:
        raise ValueError(f"{base_table} is not a sharded table")

    return [f"shard_{i}_{base}" for i in ALL_SHARDS]

# ---------- DEBUG / TEST HELPERS ----------

def shard_distribution(member_ids: List[int]) -> dict:
    """
    Utility to inspect distribution across shards.
    Useful for testing load balance.
    """
    dist = {i: 0 for i in ALL_SHARDS}
    for mid in member_ids:
        dist[get_shard_id(mid)] += 1
    return dist
