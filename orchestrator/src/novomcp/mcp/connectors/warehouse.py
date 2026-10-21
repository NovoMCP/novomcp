"""
Shared read and write for SQL warehouse connectors (Snowflake, Databricks).

Subclasses supply the connection, the dialect, and which config keys name the
default container (database/catalog and schema). Tables are given as `table`,
`schema.table` or `container.schema.table`; missing parts come from config.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from . import sql
from .base import BaseConnector, ExportIdentity, ExportResult, ReadResult, WriteMode

logger = logging.getLogger(__name__)

# Rows sent per executemany() call. A batch that fails is retried row by row,
# so one bad row doesn't lose its neighbours and the failure count is exact.
WRITE_BATCH = 500


class WarehouseConnector(BaseConnector):
    dialect: sql.Dialect
    container_key: str           # "database" (Snowflake) or "catalog" (Databricks)
    default_container: Optional[str]
    default_schema: str
    location_label: str          # "SNOWFLAKE" / "DATABRICKS"

    def _get_connection(self):  # pragma: no cover - provided by subclasses
        raise NotImplementedError

    def table_parts(self, table: str) -> List[str]:
        parts = [p for p in str(table or "").split(".")]
        if not table or len(parts) > 3 or any(not p for p in parts):
            raise ValueError(f"Invalid table: {table!r} (use table, schema.table or {self.container_key}.schema.table)")
        if len(parts) == 1:
            parts = [self.config.get("schema") or self.default_schema] + parts
        if len(parts) == 2:
            container = self.config.get(self.container_key) or self.default_container
            if not container:
                raise ValueError(f"Set a {self.container_key} on the connection, or give the table as {self.container_key}.schema.table")
            parts = [container] + parts
        for p in parts:
            sql.check_name(p, "table name")
        return parts

    def _run(self, statement: str, values: Optional[List[Any]] = None, fetch: bool = False):
        cur = self._get_connection().cursor()
        try:
            if values:
                cur.execute(statement, values)
            else:
                cur.execute(statement)
            if fetch:
                names = [c[0] for c in (cur.description or [])]
                return names, cur.fetchall()
            return None
        finally:
            cur.close()

    async def count_rows(self, table: str, filters: Optional[List[Dict[str, Any]]] = None) -> int:
        statement, values = sql.count_sql(self.dialect, self.table_parts(table), filters)
        _, rows = self._run(statement, values, fetch=True)
        return int(rows[0][0]) if rows else 0

    async def read_data(
        self,
        table: str,
        columns: Optional[List[str]] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ReadResult:
        try:
            parts = self.table_parts(table)
            statement, values = sql.select_sql(self.dialect, parts, columns, filters, max(0, int(limit)), max(0, int(offset)))
            names, fetched = self._run(statement, values, fetch=True)
            rows = [dict(zip(names, r)) for r in fetched]
            total = await self.count_rows(table, filters)
            return ReadResult(
                success=True,
                columns=names,
                rows=rows,
                row_count=len(rows),
                total_available=total,
                truncated=offset + len(rows) < total,
            )
        except Exception as e:
            logger.error(f"{self.location_label} read failed: {e}")
            return ReadResult(success=False, error=str(e))

    def _table_exists(self, parts: List[str]) -> bool:
        try:
            self._run(f"SELECT * FROM {self.dialect.table(parts)} LIMIT 0")
            return True
        except Exception:
            return False

    def _insert(self, parts: List[str], columns: List[str], rows: List[Dict[str, Any]]) -> Tuple[int, int, Optional[str]]:
        one_row = sql.insert_sql(self.dialect, parts, columns)
        values = [[sql.bind_value(r.get(c)) for c in columns] for r in rows]
        written = failed = 0
        first_error: Optional[str] = None
        if self.dialect.multirow_params:
            per_statement = max(1, self.dialect.multirow_params // max(1, len(columns)))
        else:
            per_statement = 0
        cur = self._get_connection().cursor()
        try:
            for start in range(0, len(values), per_statement or WRITE_BATCH):
                batch = values[start:start + (per_statement or WRITE_BATCH)]
                try:
                    if per_statement:
                        # Many rows in one statement: one round trip per batch.
                        cur.execute(sql.insert_sql(self.dialect, parts, columns, rows=len(batch)),
                                    [v for row in batch for v in row])
                    else:
                        cur.executemany(one_row, batch)
                    written += len(batch)
                    continue
                except Exception:
                    pass
                for row in batch:  # retry row by row so one bad row doesn't sink the batch
                    try:
                        cur.execute(one_row, row)
                        written += 1
                    except Exception as e:
                        failed += 1
                        first_error = first_error or str(e)
        finally:
            cur.close()
        return written, failed, first_error

    async def write_data(
        self,
        target: str,
        data: List[Dict[str, Any]],
        mode: WriteMode = WriteMode.APPEND,
        identity: Optional[ExportIdentity] = None,
    ) -> ExportResult:
        if not data:
            return ExportResult(success=True, rows_written=0, target_location=target)
        if mode == WriteMode.UPSERT:
            return ExportResult(success=False, error="upsert isn't supported; use append or replace", target_location=target)
        rows = [dict(r) for r in data]
        if identity:
            ExportIdentity.inject_columns(rows, identity)
        columns = list(dict.fromkeys(k for r in rows for k in r))  # every key, first-seen order
        try:
            parts = self.table_parts(target)
            for c in columns:
                sql.check_name(c, "column")
            location = f"{self.location_label}:{'.'.join(parts)}"
            created = False
            if not self._table_exists(parts):
                self._run(sql.create_table_sql(self.dialect, parts, columns, rows))
                created = True
            elif mode == WriteMode.REPLACE:
                self._run(f"TRUNCATE TABLE {self.dialect.table(parts)}")
            written, failed, first_error = self._insert(parts, columns, rows)
            return ExportResult(
                success=failed == 0,
                rows_written=written,
                rows_failed=failed,
                target_location=location,
                error=first_error if failed else None,
                details={"created_table": created, "columns": columns},
            )
        except Exception as e:
            logger.error(f"{self.location_label} write failed: {e}")
            return ExportResult(success=False, error=str(e), target_location=f"{self.location_label}:{target}")
