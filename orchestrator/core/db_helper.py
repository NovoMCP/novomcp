"""
Database Helper for Workflow Engine — Aurora PostgreSQL.

Uses psycopg2 against an Aurora writer endpoint (configured via
`AURORA_HOST` env var). Preserves the existing API surface
(DatabaseHelper, execute_sql, query_sql, async wrappers, thread-local
connection lifecycle, deadlock + transient retry).

Logical → physical mapping:
  DB_NAME_5 (research)   → Aurora "research" schema
  DB_NAME_6 (scientific) → Aurora "research" schema (consolidated)

Authentication: IRSA in-cluster, or Secrets Manager password lookup via
`AURORA_SECRET_ID`. `DB_PASSWORD` env var overrides for local dev.
"""

import logging
import os
from typing import List, Dict, Any, Tuple, Optional
import httpx
import asyncio
import json
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
import threading
import time

# psycopg2 replaces pymssql. Keep HAVE_PSYCOPG2 flag so callers that
# defensively check HAVE_PYMSSQL still work (alias both for backcompat).
try:
    import psycopg2  # type: ignore
    import psycopg2.extras
    import psycopg2.pool
    psycopg2.extras.register_uuid()
    HAVE_PSYCOPG2 = True
    HAVE_PYMSSQL = True  # legacy callers
except Exception as e:
    HAVE_PSYCOPG2 = False
    HAVE_PYMSSQL = False
    # Direct SQL is optional; local runs don't need a database.
    logging.getLogger(__name__).debug(
        "psycopg2 unavailable at import time: %s. Direct SQL will be disabled.", e
    )

logger = logging.getLogger(__name__)

# Connection pool tuning
MAX_CONNECTIONS_PER_DB = int(os.getenv("DB_MAX_CONNECTIONS", "10"))
CONNECTION_MAX_AGE_SECONDS = int(os.getenv("DB_CONNECTION_MAX_AGE_S", "300"))
CONNECTION_HEALTH_CHECK_INTERVAL = 60

# PostgreSQL database coordinates. Preferred generic env vars are
# NOVOMCP_DB_HOST / NOVOMCP_DB_PORT / etc. Legacy AURORA_* names are kept
# as fallbacks so existing deployments don't break. Any Postgres works
# (Aurora, RDS, self-hosted, Docker) — the AURORA_ prefix is historical.
AURORA_HOST = os.getenv("NOVOMCP_DB_HOST") or os.getenv("AURORA_HOST", "")
AURORA_PORT = int(os.getenv("NOVOMCP_DB_PORT") or os.getenv("AURORA_PORT", "5432"))
AURORA_DB = os.getenv("NOVOMCP_DB_NAME") or os.getenv("AURORA_DB", "postgres")
AURORA_USER = os.getenv("NOVOMCP_DB_USER") or os.getenv("AURORA_USER", "postgres")
AURORA_SECRET_ID = os.getenv("NOVOMCP_DB_SECRET_ID") or os.getenv("AURORA_SECRET_ID", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Legacy SQL Server env vars — kept so existing deployments don't break.
# DB_SERVER + DB_USER + DB_PASSWORD aren't used to connect anymore; they
# only gate "use_direct_sql" so the code mirrors the legacy behavior.
DB_SERVER = os.getenv("DB_SERVER", AURORA_HOST)
DB_USER = os.getenv("DB_USER", AURORA_USER)
DB_PASSWORD = os.getenv("DB_PASSWORD", "")  # optional override; pool fetches from SM if blank
# Legacy logical DB names. Aurora consolidates them into schemas under one
# physical DB; the public API still accepts the old names and routes them.
# Env-var-driven with generic defaults; self-hosters set DB_NAME_5 etc. to
# match their own schema layout.
RESEARCH_DB_NAME = os.getenv("DB_NAME_5", "research")
SCIENTIFIC_DB_NAME = os.getenv("DB_NAME_6", "research")

# Schema mapping: logical DB name → Aurora schema. Self-hosters map their
# own names by overriding the env vars above.
DB_NAME_TO_SCHEMA = {
    RESEARCH_DB_NAME: "research",
    SCIENTIFIC_DB_NAME: "research",
    os.getenv("DB_NAME_IDENTITY", "identity"): "identity",
    os.getenv("DB_NAME_FILES", "files"): "files",
}

USE_DIRECT_SQL = os.getenv("USE_DIRECT_SQL", "true").lower() == "true"

DB_DEADLOCK_MAX_RETRIES = int(os.getenv("DB_DEADLOCK_MAX_RETRIES", "4"))
DB_DEADLOCK_BACKOFF_BASE_MS = int(os.getenv("DB_DEADLOCK_BACKOFF_BASE_MS", "100"))
DB_CONNECT_MAX_RETRIES = int(os.getenv("DB_CONNECT_MAX_RETRIES", "3"))
DB_CONNECT_BACKOFF_BASE_MS = int(os.getenv("DB_CONNECT_BACKOFF_BASE_MS", "200"))

# Legacy db-manager proxy (unused after Aurora pivot; kept for compat).
# No production default; self-hosters set DB_MANAGER_URL if they use it.
DB_MANAGER_URL = os.getenv("DB_MANAGER_URL", "")
DB_MANAGER_API_KEY = os.getenv("DB_MANAGER_API_KEY") or os.getenv("DASHBOARD_AGGREGATOR_API_KEY", "")


def async_execute(func):
    """Run sync DB operation on the shared thread pool."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        executor = get_db_thread_pool()
        return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))
    return wrapper


_db_thread_pool = None
_pool_lock = threading.Lock()


def get_db_thread_pool() -> ThreadPoolExecutor:
    global _db_thread_pool
    if _db_thread_pool is None:
        with _pool_lock:
            if _db_thread_pool is None:
                _db_thread_pool = ThreadPoolExecutor(
                    max_workers=20, thread_name_prefix="db-pool-"
                )
                logger.info("Created database thread pool with 20 workers")
    return _db_thread_pool


# ─────────────────────────────────────────────────────────────────────────────
# Aurora password + connection helpers (module-level so shared across helpers)
# ─────────────────────────────────────────────────────────────────────────────

_aurora_password_cache: Optional[str] = None
_aurora_password_lock = threading.Lock()


def _get_aurora_password() -> Optional[str]:
    global _aurora_password_cache
    if _aurora_password_cache is not None:
        return _aurora_password_cache
    with _aurora_password_lock:
        if _aurora_password_cache is not None:
            return _aurora_password_cache
        if DB_PASSWORD:
            _aurora_password_cache = DB_PASSWORD
            return _aurora_password_cache
        # OSS local mode: neither AURORA_SECRET_ID nor DB_PASSWORD set.
        # Skip the Secrets Manager call entirely (would fail loudly with
        # 'Invalid length for parameter SecretId'). Callers get None back
        # and surface a clean 'Aurora not configured' error.
        if not AURORA_SECRET_ID:
            return None
        try:
            import boto3
            sm = boto3.client("secretsmanager", region_name=AWS_REGION)
            raw = sm.get_secret_value(SecretId=AURORA_SECRET_ID)["SecretString"]
            try:
                d = json.loads(raw)
                pw = d.get("password") or d.get("Password") or next(iter(d.values()))
            except json.JSONDecodeError:
                pw = raw.strip()
            _aurora_password_cache = pw
            return pw
        except Exception as e:
            logger.warning(f"Failed to fetch Aurora password from Secrets Manager: {e}")
            return None


class DatabaseHelper:
    """
    Async database helper with direct Aurora PostgreSQL support.

    Thread-local connection cache + age recycling. Each connection is
    bound to the schema for its logical DB name via SET search_path.
    """

    def __init__(self):
        self.base_url = DB_MANAGER_URL
        self.api_key = DB_MANAGER_API_KEY
        self._thread_local = threading.local()
        self._pool_lock = threading.Lock()
        self.use_direct_sql = bool(USE_DIRECT_SQL and HAVE_PSYCOPG2)

        logger.info("=" * 60)
        logger.info("DATABASE CONFIGURATION (Aurora pivot)")
        logger.info(f"  Aurora host: {AURORA_HOST}:{AURORA_PORT}/{AURORA_DB}")
        logger.info(f"  Schema map:  research={DB_NAME_TO_SCHEMA.get(RESEARCH_DB_NAME, 'research')}, "
                    f"scientific={DB_NAME_TO_SCHEMA.get(SCIENTIFIC_DB_NAME, 'research')}")
        logger.info(f"  Direct SQL: {'ON' if self.use_direct_sql else 'OFF'}")
        logger.info(f"  Connection max age: {CONNECTION_MAX_AGE_SECONDS}s")
        logger.info("=" * 60)

    def _check_connection(self, conn) -> bool:
        if not conn:
            return False
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except Exception as e:
            logger.debug(f"Connection check failed: {e}")
            return False

    def _is_connection_error(self, exc: Exception) -> bool:
        """Detect transient connection errors. Covers both pymssql-era and psycopg2 messages."""
        msg = str(exc) if exc else ""
        keywords = (
            "Adaptive Server connection timed out",  # legacy SQL Server msgs (callers may still match)
            "Login timeout expired",
            "timed out",
            "TimeoutError",
            "Broken pipe",
            "Connection reset by peer",
            "connection closed",
            "cannot connect",
            "Network is unreachable",
            "EHOSTUNREACH",
            "ECONNRESET",
            "OperationalError",
            "server closed the connection unexpectedly",  # psycopg2 transient
            "could not connect to server",                # psycopg2 startup transient
            "SSL connection has been closed unexpectedly",
        )
        if any(k.lower() in msg.lower() for k in keywords):
            return True
        try:
            if hasattr(exc, "args") and exc.args:
                return any(
                    isinstance(a, (str, bytes)) and any(k.lower() in str(a).lower() for k in keywords)
                    for a in exc.args
                )
        except Exception:
            pass
        if HAVE_PSYCOPG2 and isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
            return True
        return False

    def _get_connection(self, database: str):
        """Get / create / recycle an Aurora connection bound to the schema for `database`."""
        if not HAVE_PSYCOPG2:
            raise RuntimeError("psycopg2 is not available. Install psycopg2-binary.")
        if not database:
            raise RuntimeError(
                "Target database name is not configured "
                "(DB_NAME_5/DB_NAME_6 missing)."
            )
        schema = DB_NAME_TO_SCHEMA.get(database, "research")

        if not hasattr(self._thread_local, "connections"):
            self._thread_local.connections = {}
        if not hasattr(self._thread_local, "connection_ages"):
            self._thread_local.connection_ages = {}

        connections = self._thread_local.connections
        connection_ages = self._thread_local.connection_ages

        needs_reconnect = False
        if database in connections:
            connection_age = time.time() - connection_ages.get(database, time.time())
            if connection_age > CONNECTION_MAX_AGE_SECONDS:
                logger.debug(
                    f"Connection to {database} ({schema}) is {connection_age:.0f}s old "
                    f"(max: {CONNECTION_MAX_AGE_SECONDS}s), recycling"
                )
                needs_reconnect = True

        if (
            needs_reconnect
            or database not in connections
            or not self._check_connection(connections.get(database))
        ):
            try:
                logger.debug(
                    f"Creating new Aurora connection for {database} -> schema={schema} "
                    f"(thread ID: {threading.get_ident()})"
                )
                if database in connections:
                    try:
                        connections[database].close()
                    except Exception:
                        pass

                # Fail fast + cleanly when the database isn't configured at
                # all (typical in OSS local mode). Error message is generic —
                # "Aurora" is our hosted deploy's flavor of Postgres, but any
                # PostgreSQL works. Callers already handle exceptions.
                if not AURORA_HOST:
                    raise RuntimeError(
                        "PostgreSQL database not configured. "
                        "The omics tools (target_discovery, stratify_patients) "
                        "require a Postgres database with the omics schema loaded. "
                        "Set NOVOMCP_DB_HOST (or AURORA_HOST) to enable. "
                        "See docs/optional-data-services.md."
                    )
                password = _get_aurora_password()
                if not password:
                    raise RuntimeError(
                        "PostgreSQL password not available. "
                        "Set DB_PASSWORD directly (recommended for local) or "
                        "NOVOMCP_DB_SECRET_ID / AURORA_SECRET_ID for AWS "
                        "Secrets Manager lookups. "
                        "See docs/optional-data-services.md."
                    )

                conn = psycopg2.connect(
                    host=AURORA_HOST, port=AURORA_PORT,
                    dbname=AURORA_DB, user=AURORA_USER, password=password,
                    application_name="novomcp",
                    connect_timeout=30,
                    sslmode="require",
                    options=f"-c search_path={schema},data,public",
                )
                conn.autocommit = False
                connections[database] = conn
                connection_ages[database] = time.time()
                logger.debug(f"Connected to Aurora schema={schema}")
            except Exception as e:
                logger.error(f"Failed to connect to Aurora schema={schema}: {e}")
                raise

        return connections[database]

    def _execute_direct(self, sql: str, params: Optional[Tuple], database: str) -> Dict[str, Any]:
        """Execute SQL directly against Aurora with reconnect on transient errors."""
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt < max(1, DB_CONNECT_MAX_RETRIES):
            conn = None
            cursor = None
            try:
                conn = self._get_connection(database)
                # RealDictCursor preserves the legacy as_dict=True row shape.
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                if sql.strip().upper().startswith("SELECT") or " RETURNING " in sql.upper():
                    results = cursor.fetchall()
                    cursor.close()
                    conn.commit()
                    return {"success": True, "data": [dict(r) for r in results], "count": len(results)}
                else:
                    affected = cursor.rowcount
                    conn.commit()
                    cursor.close()
                    return {"success": True, "affected_rows": affected}
            except Exception as e:
                last_exc = e
                try:
                    if conn:
                        conn.rollback()
                except Exception:
                    pass
                try:
                    if cursor:
                        cursor.close()
                except Exception:
                    pass

                if self._is_connection_error(e) and attempt < DB_CONNECT_MAX_RETRIES - 1:
                    try:
                        if (
                            hasattr(self._thread_local, "connections")
                            and database in getattr(self._thread_local, "connections", {})
                        ):
                            try:
                                self._thread_local.connections[database].close()
                            except Exception:
                                pass
                            del self._thread_local.connections[database]
                        if (
                            hasattr(self._thread_local, "connection_ages")
                            and database in getattr(self._thread_local, "connection_ages", {})
                        ):
                            del self._thread_local.connection_ages[database]
                    except Exception:
                        pass

                    backoff_ms = DB_CONNECT_BACKOFF_BASE_MS * (2 ** attempt)
                    logger.warning(
                        "DB connection error; retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": DB_CONNECT_MAX_RETRIES,
                            "backoff_ms": backoff_ms,
                            "database": database,
                            "error": str(e)[:200],
                        },
                    )
                    time.sleep(backoff_ms / 1000.0)
                    attempt += 1
                    continue

                raise

        raise last_exc if last_exc else RuntimeError("DB execution failed after retries")

    def _is_deadlock_error(self, exc: Exception) -> bool:
        """Detect PostgreSQL deadlock_detected (SQLSTATE 40P01) or serialization failure (40001).
        Also matches legacy SQL Server msgs in case the message bubbles up from a caller."""
        msg = str(exc) if exc else ""
        if any(code in msg for code in ("40P01", "40001", "deadlock", "Deadlock", "1205")):
            return True
        if HAVE_PSYCOPG2 and hasattr(exc, "pgcode") and exc.pgcode in ("40P01", "40001"):
            return True
        try:
            if hasattr(exc, "args") and exc.args:
                return any(
                    isinstance(a, (str, bytes))
                    and any(code in str(a) for code in ("40P01", "40001", "1205"))
                    for a in exc.args
                )
        except Exception:
            pass
        return False

    async def execute_sql(self, sql: str, params: Optional[Tuple] = None, database: str = None) -> Dict[str, Any]:
        """Execute SQL statement with deadlock retries (direct SQL)."""
        if not self.use_direct_sql:
            raise Exception("Direct SQL is disabled but required for autonomous operation")
        if database is None:
            database = RESEARCH_DB_NAME

        attempt = 0
        while True:
            try:
                return await async_execute(lambda: self._execute_direct(sql, params, database))()
            except Exception as e:
                if attempt < DB_DEADLOCK_MAX_RETRIES and self._is_deadlock_error(e):
                    backoff_ms = DB_DEADLOCK_BACKOFF_BASE_MS * (2 ** attempt)
                    logger.warning(
                        "DB deadlock detected; retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": DB_DEADLOCK_MAX_RETRIES,
                            "backoff_ms": backoff_ms,
                            "database": database,
                        },
                    )
                    await asyncio.sleep(backoff_ms / 1000.0)
                    attempt += 1
                    continue
                raise

    async def query_sql(self, sql: str, params: Optional[Tuple] = None, database: str = None) -> List[Dict[str, Any]]:
        """Execute SQL query with deadlock retries (direct SQL)."""
        if not self.use_direct_sql:
            raise Exception("Direct SQL is disabled but required for autonomous operation")
        if database is None:
            database = RESEARCH_DB_NAME

        attempt = 0
        while True:
            try:
                result = await async_execute(lambda: self._execute_direct(sql, params, database))()
                if result.get("success"):
                    return result.get("data", [])
                return []
            except Exception as e:
                if attempt < DB_DEADLOCK_MAX_RETRIES and self._is_deadlock_error(e):
                    backoff_ms = DB_DEADLOCK_BACKOFF_BASE_MS * (2 ** attempt)
                    logger.warning(
                        "DB deadlock detected on SELECT; retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": DB_DEADLOCK_MAX_RETRIES,
                            "backoff_ms": backoff_ms,
                            "database": database,
                        },
                    )
                    await asyncio.sleep(backoff_ms / 1000.0)
                    attempt += 1
                    continue
                raise


_db_helper = None


def get_db_helper() -> DatabaseHelper:
    global _db_helper
    if _db_helper is None:
        _db_helper = DatabaseHelper()
    return _db_helper


async def execute_sql(sql: str, params: Optional[Tuple] = None, database: str = None) -> Dict[str, Any]:
    helper = get_db_helper()
    return await helper.execute_sql(sql, params, database)


async def query_sql(sql: str, params: Optional[Tuple] = None, database: str = None) -> List[Dict[str, Any]]:
    helper = get_db_helper()
    return await helper.query_sql(sql, params, database)
