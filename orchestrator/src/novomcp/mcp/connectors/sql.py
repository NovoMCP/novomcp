"""
Safe SQL for the warehouse connectors (Snowflake, Databricks).

Names (tables, columns) can't be sent as bound parameters, so each one must
match a strict pattern before it is written into a statement. Every value is
bound. Filters are structured (column, operator from a fixed list, value), so
callers never pass raw SQL.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "LIKE", "IN", "IS_NULL", "IS_NOT_NULL")


def check_name(name: Any, what: str = "name") -> str:
    """Return `name` if it is a plain SQL name, else raise ValueError."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"Invalid {what}: {name!r} (letters, digits and _ only, not starting with a digit)")
    return name


@dataclass(frozen=True)
class Dialect:
    """How one warehouse writes names and parameters.

    quote: wraps each name ("" leaves names bare, which Snowflake folds to
      upper case, matching tables created without quotes).
    param: the driver's positional placeholder.
    types: native column type for each Python value type, used when a table
      is created from the rows being written.
    multirow_params: when set, writes send many rows per INSERT (up to this
      many bound values per statement) instead of the driver's executemany,
      which for Databricks is one round trip per row.
    """
    quote: str
    param: str
    types: Dict[str, str]
    multirow_params: int = 0

    def name(self, name: str, what: str = "name") -> str:
        return f"{self.quote}{check_name(name, what)}{self.quote}"

    def table(self, parts: Sequence[str]) -> str:
        return ".".join(self.name(p, "table name") for p in parts)


SNOWFLAKE = Dialect(quote="", param="%s", types={
    "bool": "BOOLEAN", "int": "NUMBER", "float": "FLOAT", "str": "VARCHAR",
})
DATABRICKS = Dialect(quote="`", param="?", types={
    "bool": "BOOLEAN", "int": "BIGINT", "float": "DOUBLE", "str": "STRING",
}, multirow_params=250)


def where_clause(d: Dialect, filters: Optional[List[Dict[str, Any]]]) -> Tuple[str, List[Any]]:
    """' WHERE ...' (or '') and its bound values."""
    if not filters:
        return "", []
    parts: List[str] = []
    values: List[Any] = []
    for f in filters:
        col = d.name(f.get("column"), "filter column")
        op = str(f.get("operator", "")).upper()
        if op not in OPERATORS:
            raise ValueError(f"Invalid filter operator: {op!r}. Use one of {', '.join(OPERATORS)}")
        if op == "IS_NULL":
            parts.append(f"{col} IS NULL")
        elif op == "IS_NOT_NULL":
            parts.append(f"{col} IS NOT NULL")
        elif op == "IN":
            items = f.get("value")
            if not isinstance(items, list) or not items:
                raise ValueError(f"IN needs a non-empty list for {f.get('column')!r}")
            parts.append(f"{col} IN ({', '.join([d.param] * len(items))})")
            values.extend(items)
        else:
            parts.append(f"{col} {op} {d.param}")
            values.append(f.get("value"))
    return " WHERE " + " AND ".join(parts), values


def select_sql(
    d: Dialect,
    table: Sequence[str],
    columns: Optional[List[str]],
    filters: Optional[List[Dict[str, Any]]],
    limit: int,
    offset: int = 0,
) -> Tuple[str, List[Any]]:
    cols = ", ".join(d.name(c, "column") for c in columns) if columns else "*"
    where, values = where_clause(d, filters)
    return f"SELECT {cols} FROM {d.table(table)}{where} LIMIT {int(limit)} OFFSET {int(offset)}", values


def count_sql(d: Dialect, table: Sequence[str], filters: Optional[List[Dict[str, Any]]]) -> Tuple[str, List[Any]]:
    where, values = where_clause(d, filters)
    return f"SELECT COUNT(*) FROM {d.table(table)}{where}", values


def insert_sql(d: Dialect, table: Sequence[str], columns: List[str], rows: int = 1) -> str:
    cols = ", ".join(d.name(c, "column") for c in columns)
    one = f"({', '.join([d.param] * len(columns))})"
    return f"INSERT INTO {d.table(table)} ({cols}) VALUES {', '.join([one] * rows)}"


def _type_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"  # strings, and dicts/lists stored as JSON text


def create_table_sql(d: Dialect, table: Sequence[str], columns: List[str], rows: List[Dict[str, Any]]) -> str:
    """CREATE TABLE with each column typed from the first non-null value seen.
    A column whose values mix numbers and text is stored as text."""
    defs = []
    for c in columns:
        kinds = {k for k in (_type_key(r.get(c)) for r in rows[:200]) if k}
        if kinds == {"int", "float"}:
            kind = "float"
        elif len(kinds) == 1:
            kind = kinds.pop()
        else:
            kind = "str"
        defs.append(f"{d.name(c, 'column')} {d.types[kind]}")
    return f"CREATE TABLE {d.table(table)} ({', '.join(defs)})"


def bind_value(value: Any) -> Any:
    """Nested values go in as JSON text; everything else as is."""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return value
