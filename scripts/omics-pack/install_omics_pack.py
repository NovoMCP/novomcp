#!/usr/bin/env python3
"""Install one or more omics SQLite packs into the engine's local omics DB.

Merges each pack's tables (schema + data + indexes) into a single
`~/.novo/omics/omics.db` (or $NOVOMCP_OMICS_DB) — the file the engine ATTACHes
as schema `omics` (see core/db_helper.py). Packs hold disjoint tables:

    omics-core.sqlite[.gz]  -> omics_targets, omics_perturbation, omics_resistance
    omics-pgx.sqlite[.gz]   -> omics_pgx  (separate ShareAlike pack, opt-in)

Install whichever packs you have; re-installing replaces those tables (idempotent).
`target_discovery` needs only the core pack; `stratify_patients`'s PGx layer needs
the pgx pack too. With no pgx pack installed, the PGx lookup simply returns empty.

    python install_omics_pack.py omics-core.sqlite.gz [omics-pgx.sqlite.gz]
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

DEFAULT_DB = os.getenv("NOVOMCP_OMICS_DB") or os.path.expanduser("~/.novo/omics/omics.db")


def _as_sqlite(path: Path) -> tuple[Path, bool]:
    """Return a plain-.sqlite path, decompressing a .gz to a temp file."""
    if path.suffix == ".gz":
        tmp = Path(tempfile.mkstemp(suffix=".sqlite")[1])
        with gzip.open(path, "rb") as f_in, open(tmp, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        return tmp, True
    return path, False


def _merge(target: sqlite3.Connection, pack: Path) -> list[str]:
    src, is_temp = _as_sqlite(pack)
    installed: list[str] = []
    try:
        target.execute("ATTACH DATABASE ? AS src", (str(src),))
        objs = target.execute(
            "SELECT type, name, sql FROM src.sqlite_master "
            "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for typ, name, sql in [o for o in objs if o[0] == "table"]:
            target.execute(f'DROP TABLE IF EXISTS main."{name}"')
            target.execute(sql)  # recreate with identical DDL (affinity preserved)
            target.execute(f'INSERT INTO main."{name}" SELECT * FROM src."{name}"')
            installed.append(name)
        for typ, name, sql in [o for o in objs if o[0] == "index" and o[2]]:
            target.execute(sql)
        target.commit()
        target.execute("DETACH DATABASE src")
    finally:
        if is_temp:
            src.unlink(missing_ok=True)
    return installed


def main() -> None:
    ap = argparse.ArgumentParser(description="Install omics SQLite pack(s) into the local omics DB.")
    ap.add_argument("packs", nargs="+", help="omics-core.sqlite[.gz] and/or omics-pgx.sqlite[.gz]")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"target omics DB (default {DEFAULT_DB})")
    args = ap.parse_args()

    db_path = Path(os.path.expanduser(args.db))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        for p in args.packs:
            pack = Path(p)
            if not pack.exists():
                sys.exit(f"pack not found: {pack}")
            tables = _merge(conn, pack)
            print(f"installed {pack.name}: {', '.join(tables)}", file=sys.stderr)
    finally:
        conn.close()
    print(f"omics DB ready at {db_path} — the engine's omics tools will use it.", file=sys.stderr)


if __name__ == "__main__":
    main()
