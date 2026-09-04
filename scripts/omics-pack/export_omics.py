#!/usr/bin/env python3
"""Export the omics schema from Postgres to the OSS SQLite data packs.

Produces two license-distinct SQLite files:

  * omics-core.sqlite  — omics_targets + omics_perturbation + omics_resistance
    (Open Targets / UniProt / Reactome / Perturb-seq / ClinVar — permissive /
    attribution sources; ships as a CC-BY data pack)
  * omics-pgx.sqlite    — omics_pgx (PharmGKB CC-BY-SA + CPIC CC0 + gnomAD ODbL;
    ShareAlike — ships as a separate opt-in pack)

Only the columns the OSS engine actually reads are exported (the executors'
SELECT lists) — nothing internal rides along. Postgres array / JSONB columns
are serialized to JSON text so the engine's SQLite route decodes them back to
the same list/dict shape psycopg2 would return (see core/db_helper.py).

Run against a Postgres holding the `omics` schema (Aurora or self-hosted),
using the standard NOVOMCP_DB_* / DB_PASSWORD env vars:

    NOVOMCP_DB_HOST=... DB_PASSWORD=... python export_omics.py --out ./dist
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# psycopg2 is imported lazily inside _pg_connect() — only the actual export
# needs it, so the table spec / serialization can be imported and tested
# (and this module linted) without a Postgres driver installed.

# Per-table export spec: the exact columns the OSS executors read, with SQLite
# affinities (REAL/INTEGER so ORDER BY sorts numerically; TEXT otherwise) and
# the index the query filters/orders on. JSON/array columns are stored TEXT.
TABLES = {
    "omics_targets": {
        "pack": "core",
        "columns": [
            ("id", "INTEGER"), ("gene_symbol", "TEXT"), ("ensembl_id", "TEXT"),
            ("uniprot_id", "TEXT"), ("disease_efo_id", "TEXT"), ("disease_name", "TEXT"),
            ("composite_score", "REAL"), ("overall_score", "REAL"),
            ("genetic_score", "REAL"), ("expression_score", "REAL"),
            ("best_pdb_resolution_a", "REAL"), ("pdb_ids", "TEXT"),
            ("key_variants", "TEXT"), ("top_pathways", "TEXT"),
            ("suggested_pdb_id", "TEXT"), ("pdb_selection_criteria", "TEXT"),
            ("known_drugs_count", "INTEGER"), ("tractability_small_molecule", "REAL"),
            ("source_version", "TEXT"),
        ],
        "indexes": [("ix_targets_disease", "disease_efo_id")],
    },
    "omics_perturbation": {
        "pack": "core",
        "columns": [
            ("gene_symbol", "TEXT"), ("dataset_source", "TEXT"),
            ("perturbation_score", "REAL"), ("n_overlap_up", "INTEGER"),
            ("n_overlap_down", "INTEGER"), ("signature_version", "TEXT"),
            ("perturbation_data_version", "TEXT"), ("license_tag", "TEXT"),
            ("disease_efo_id", "TEXT"),
        ],
        "indexes": [("ix_pert_disease", "disease_efo_id"), ("ix_pert_gene", "gene_symbol")],
    },
    "omics_resistance": {
        "pack": "core",
        "columns": [
            ("gene_symbol", "TEXT"), ("variant", "TEXT"), ("cancer_type", "TEXT"),
            ("clinvar_significance", "TEXT"), ("affects_binding_site", "INTEGER"),
        ],
        "indexes": [("ix_resist_gene", "gene_symbol")],
    },
    "omics_pgx": {
        "pack": "pgx",
        "columns": [
            ("gene_symbol", "TEXT"), ("cpic_level", "TEXT"), ("gene_function", "TEXT"),
            ("clinical_implications", "TEXT"), ("key_alleles", "TEXT"),
            ("metabolizer_phenotypes", "TEXT"), ("population_frequencies", "TEXT"),
            ("variant_count_gnomad", "INTEGER"),
        ],
        "indexes": [("ix_pgx_gene", "gene_symbol")],
    },
}


def _pg_connect():
    import psycopg2  # lazy: only the export path needs the driver
    host = os.getenv("NOVOMCP_DB_HOST") or os.getenv("AURORA_HOST")
    if not host:
        sys.exit("Set NOVOMCP_DB_HOST (or AURORA_HOST) + DB_PASSWORD to export.")
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("NOVOMCP_DB_PORT") or os.getenv("AURORA_PORT", "5432")),
        dbname=os.getenv("NOVOMCP_DB_NAME") or os.getenv("AURORA_DB", "postgres"),
        user=os.getenv("NOVOMCP_DB_USER") or os.getenv("AURORA_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        sslmode=os.getenv("PGSSLMODE", "require"),
        options="-c search_path=omics,public",
    )


def _serialize(v):
    """Match the engine's read path: arrays/JSONB -> JSON text, scalars as-is."""
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return v


def _export_table(pg, table: str, spec: dict, sq: sqlite3.Connection, where: str = "") -> int:
    cols = [c for c, _ in spec["columns"]]
    ddl_cols = ", ".join(f'"{c}" {t}' for c, t in spec["columns"])
    sq.execute(f'CREATE TABLE "{table}" ({ddl_cols})')
    placeholders = ", ".join("?" for _ in cols)
    insert = f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({placeholders})'

    # Plain positional cursor: rows come back as tuples in `cols` order.
    cur = pg.cursor()
    cur.execute(f'SELECT {", ".join(cols)} FROM omics.{table} {where}')
    n = 0
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        sq.executemany(insert, [[_serialize(v) for v in row] for row in batch])
        n += len(batch)
    cur.close()
    for name, col in spec["indexes"]:
        sq.execute(f'CREATE INDEX "{name}" ON "{table}" ("{col}")')
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Export omics schema -> SQLite packs.")
    ap.add_argument("--out", default="./dist", help="output directory")
    ap.add_argument("--gzip", action="store_true", help="also write .sqlite.gz")
    ap.add_argument(
        "--perturbation-licenses", default="",
        help="comma-separated allowlist of permissive omics_perturbation license_tag "
             "values to export. REQUIRED to include omics_perturbation — the table can "
             "carry NonCommercial-licensed rows (e.g. DisGeNET, CC-BY-NC-SA), so it is "
             "SKIPPED by default. Run once without this flag to print the distinct tags, "
             "then re-run allowlisting only the permissive ones.",
    )
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pg = _pg_connect()
    try:
        for pack in ("core", "pgx"):
            path = out / f"omics-{pack}.sqlite"
            if path.exists():
                path.unlink()
            sq = sqlite3.connect(str(path))
            try:
                total = 0
                for table, spec in TABLES.items():
                    if spec["pack"] != pack:
                        continue
                    where = ""
                    if table == "omics_perturbation":
                        # NonCommercial-risk table: report the license_tag distribution,
                        # and export ONLY explicitly-allowlisted permissive tags. Skipped
                        # by default so a stray NonCommercial row (e.g. DisGeNET,
                        # CC-BY-NC-SA) can never leak into the CC-BY core pack.
                        tc = pg.cursor()
                        tc.execute("SELECT license_tag, count(*) FROM omics.omics_perturbation "
                                   "GROUP BY license_tag ORDER BY 2 DESC")
                        dist = tc.fetchall()
                        tc.close()
                        print("  omics_perturbation license_tag distribution:", file=sys.stderr)
                        for tag, cnt in dist:
                            print(f"    {tag!r}: {cnt:,}", file=sys.stderr)
                        allow = [t.strip() for t in args.perturbation_licenses.split(",") if t.strip()]
                        if not allow:
                            print("  SKIP omics_perturbation — no --perturbation-licenses allowlist "
                                  "(exclude NonCommercial rows explicitly; target_discovery degrades "
                                  "gracefully without this channel).", file=sys.stderr)
                            continue
                        quoted = ",".join("'" + t.replace("'", "''") + "'" for t in allow)
                        where = f"WHERE license_tag IN ({quoted})"
                        print(f"  including omics_perturbation rows with license_tag in {allow}", file=sys.stderr)
                    rows = _export_table(pg, table, spec, sq, where)
                    print(f"  {table}: {rows:,} rows", file=sys.stderr)
                    total += rows
                sq.commit()
            finally:
                sq.close()
            print(f"wrote {path} ({total:,} rows)", file=sys.stderr)
            if args.gzip:
                with open(path, "rb") as f_in, gzip.open(f"{path}.gz", "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                print(f"wrote {path}.gz", file=sys.stderr)
    finally:
        pg.close()


if __name__ == "__main__":
    main()
