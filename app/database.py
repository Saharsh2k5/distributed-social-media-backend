import os
from typing import Any, Callable, TypeVar

import pymysql
from pymysql.cursors import DictCursor

# ---------- CONFIG ----------

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_NAME = os.getenv("DB_NAME", "college_social_media")

SHARD_HOST = os.getenv("SHARD_HOST", DB_HOST)
SHARD_DB = os.getenv("SHARD_DB", DB_NAME)

def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}

USE_DISTRIBUTED_SHARDS = _env_flag("USE_DISTRIBUTED_SHARDS", "1")

def _parse_ports():
    raw = os.getenv("SHARD_PORTS", "3307,3308,3309")
    return [int(p.strip()) for p in raw.split(",") if p.strip().isdigit()]

SHARD_PORTS = _parse_ports()

T = TypeVar("T")

# ---------- ERRORS ----------

class DatabaseQueryError(Exception):
    def __init__(self, message: str, error_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code

# ---------- CONNECTIONS ----------

def get_db_connection(*, autocommit=True):
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=DictCursor,
        autocommit=autocommit,
    )

def get_shard_connection(shard_id: int, *, autocommit=True):
    return pymysql.connect(
        host=SHARD_HOST,
        port=SHARD_PORTS[shard_id],
        user=DB_USER,
        password=DB_PASSWORD,
        database=SHARD_DB,
        cursorclass=DictCursor,
        autocommit=autocommit,
    )

# ---------- AUDIT ----------

def _apply_audit(cursor, ctx):
    cursor.execute(
        """
        SET @api_authorized=%s,
        @api_actor_id=%s,
        @api_action=%s
        """,
        (1, ctx.get("actor_id"), ctx.get("action")),
    )

# ---------- QUERY HELPERS ----------

def execute_query(query, params=None, *, fetchone=False, fetchall=False, audit=None):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            if audit:
                _apply_audit(cursor, audit)
            cursor.execute(query, params)

            if fetchone:
                return cursor.fetchone()
            if fetchall:
                return cursor.fetchall()
            return cursor.lastrowid
    except pymysql.MySQLError as e:
        raise DatabaseQueryError("Query failed", e.args[0] if e.args else None)
    finally:
        if conn:
            conn.close()

def execute_query_on_shard(shard_id, query, params=None, *, fetchone=False, fetchall=False, audit=None):
    conn = None
    try:
        conn = get_shard_connection(shard_id)
        with conn.cursor() as cursor:
            if audit:
                _apply_audit(cursor, audit)
            cursor.execute(query, params)

            if fetchone:
                return cursor.fetchone()
            if fetchall:
                return cursor.fetchall()
            return cursor.lastrowid
    finally:
        if conn:
            conn.close()

def execute_query_all_shards(query, params=None):
    results = []
    for sid in range(len(SHARD_PORTS)):
        rows = execute_query_on_shard(sid, query, params, fetchall=True)
        results.extend(rows)
    return results

# ---------- TRANSACTIONS ----------

def execute_transaction(fn, *, audit=None):
    conn = None
    try:
        conn = get_db_connection(autocommit=False)
        with conn.cursor() as cursor:
            if audit:
                _apply_audit(cursor, audit)
            result = fn(cursor)
            conn.commit()
            return result
    except:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def execute_transaction_on_shard(shard_id, fn, *, audit=None):
    conn = None
    try:
        conn = get_shard_connection(shard_id, autocommit=False)
        with conn.cursor() as cursor:
            if audit:
                _apply_audit(cursor, audit)
            result = fn(cursor)
            conn.commit()
            return result
    except:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def is_distributed():
    return USE_DISTRIBUTED_SHARDS