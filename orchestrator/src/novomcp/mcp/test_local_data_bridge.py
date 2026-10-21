"""Self-hosted data connectors: safe SQL, the shared warehouse read/write, and
push_to_destination / pull_from_source running through the local bridge.

SQLite stands in for the warehouse: its cursor has the same execute /
executemany / description / fetchall surface as the Snowflake and Databricks
drivers."""
import json
import sqlite3

import pytest

import novomcp.mcp.tools as tools
from novomcp.mcp.connectors import local_bridge, sql
from novomcp.mcp.connectors.base import ConnectorType, ExportIdentity, WriteMode
from novomcp.mcp.connectors.warehouse import WarehouseConnector
from novomcp.mcp.spine import FileAuditSink, LocalAuthGate, NoopMeter, Spine
from novomcp.mcp.tools import MCPToolExecutor

SQLITE = sql.Dialect(quote='"', param="?", types={"bool": "INTEGER", "int": "INTEGER", "float": "REAL", "str": "TEXT"})


class SqliteWarehouse(WarehouseConnector):
    connector_type = ConnectorType.SNOWFLAKE
    dialect = SQLITE
    container_key = "database"
    default_container = None
    default_schema = "main"
    location_label = "SQLITE"

    def __init__(self, config, credentials):
        super().__init__(config, credentials)
        self._conn = None

    def _get_connection(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.config["path"], isolation_level=None)
        return self._conn

    def table_parts(self, table):  # SQLite names are schema.table at most
        return ["main", sql.check_name(table.split(".")[-1], "table name")]

    async def test_connection(self):
        return True, None

    async def discover_schema(self):
        return []

    @staticmethod
    def get_config_schema():
        return {}

    @staticmethod
    def get_credentials_schema():
        return {}

    async def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ── safe SQL ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["t; DROP TABLE x", "a b", "1abc", "", "x\"y", "a-b"])
def test_names_that_could_inject_are_rejected(bad):
    with pytest.raises(ValueError):
        sql.check_name(bad)


def test_filters_bind_values_and_only_allow_listed_operators():
    stmt, values = sql.select_sql(sql.DATABRICKS, ["main", "default", "cpds"], ["smiles"],
                                  [{"column": "mw", "operator": "<", "value": 500},
                                   {"column": "id", "operator": "IN", "value": [1, 2]},
                                   {"column": "note", "operator": "IS_NULL"}], limit=10)
    assert stmt == ("SELECT `smiles` FROM `main`.`default`.`cpds` WHERE `mw` < ? AND `id` IN (?, ?) "
                    "AND `note` IS NULL LIMIT 10 OFFSET 0")
    assert values == [500, 1, 2]
    with pytest.raises(ValueError):
        sql.where_clause(sql.SNOWFLAKE, [{"column": "a", "operator": "= 1 OR 1", "value": 1}])


def test_snowflake_names_are_bare_and_use_its_placeholder():
    stmt, _ = sql.count_sql(sql.SNOWFLAKE, ["DB", "PUBLIC", "T"], [{"column": "a", "operator": "=", "value": 1}])
    assert stmt == "SELECT COUNT(*) FROM DB.PUBLIC.T WHERE a = %s"


def test_table_names_fill_in_from_the_connection():
    from novomcp.mcp.connectors.databricks_connector import DatabricksConnector
    from novomcp.mcp.connectors.snowflake_connector import SnowflakeConnector

    db = DatabricksConnector({"server_hostname": "h", "http_path": "p", "catalog": "lab"}, {})
    assert db.table_parts("hits") == ["lab", "default", "hits"]
    assert db.table_parts("s.hits") == ["lab", "s", "hits"]
    sf = SnowflakeConnector({"account": "a"}, {})
    with pytest.raises(ValueError):  # no database to put it in
        sf.table_parts("hits")
    assert sf.table_parts("DB.S.HITS") == ["DB", "S", "HITS"]


# ── shared warehouse read/write ───────────────────────────────────────────


async def test_write_creates_the_table_then_reads_back(tmp_path):
    wh = SqliteWarehouse({"path": str(tmp_path / "w.db")}, {})
    ident = ExportIdentity(user_id="local", export_id="exp_1", exported_at="2026-10-21T00:00:00Z")
    rows = [{"smiles": "CCO", "mw": 46.07, "pass": True, "flags": ["a"]},
            {"smiles": "CCN", "mw": 45, "pass": False, "flags": []}]
    out = await wh.write_data("hits", rows, identity=ident)
    assert out.success and out.rows_written == 2 and out.details["created_table"]
    assert out.target_location == "SQLITE:main.hits"
    assert "_export_id" in out.details["columns"]

    again = await wh.write_data("hits", [{"smiles": "C", "mw": 16, "pass": True, "flags": None}])
    assert again.success and not again.details["created_table"]

    got = await wh.read_data("hits", columns=["smiles", "flags"], filters=[{"column": "mw", "operator": ">", "value": 20}], limit=1)
    assert got.success and got.row_count == 1 and got.total_available == 2 and got.truncated
    assert json.loads(got.rows[0]["flags"]) == ["a"]  # nested values stored as JSON text
    assert await wh.count_rows("hits") == 3
    await wh.close()


async def test_upsert_is_refused_and_bad_columns_fail_cleanly(tmp_path):
    wh = SqliteWarehouse({"path": str(tmp_path / "w.db")}, {})
    assert not (await wh.write_data("t", [{"a": 1}], mode=WriteMode.UPSERT)).success
    bad = await wh.write_data("t", [{"a b": 1}])
    assert not bad.success and "Invalid column" in bad.error
    await wh.close()


# ── local bridge ──────────────────────────────────────────────────────────


@pytest.fixture
def saved(monkeypatch, tmp_path):
    path = tmp_path / "connectors.json"
    conns = [{"id": "conn_1", "type": "snowflake", "displayName": "Lab warehouse",
              "config": {"account": "a", "path": str(tmp_path / "w.db")}, "credentials": {"password": "pw"}}]
    path.write_text(json.dumps(conns))
    monkeypatch.setenv("NOVO_CONNECTORS_PATH", str(path))
    monkeypatch.setenv("NOVO_AUTH", "local")
    monkeypatch.delenv("BRIDGE_URL", raising=False)
    monkeypatch.setattr(local_bridge, "_connector", lambda c: SqliteWarehouse(c["config"], c["credentials"]))
    return tmp_path


def test_connections_are_found_by_id_name_or_type_and_never_show_credentials(saved):
    for ref in ("conn_1", "lab warehouse", "snowflake", None):
        conn, err = local_bridge.find_connection(ref)
        assert conn and conn["id"] == "conn_1", (ref, err)
    assert local_bridge.find_connection("nope")[0] is None
    assert "credentials" not in local_bridge.describe(conn) and "password" not in json.dumps(local_bridge.describe(conn))


def test_awkward_keys_become_safe_unique_columns():
    rows = local_bridge.safe_columns([{"Lipinski pass": 1, "lipinski_pass": 2, "3d": 3}])
    assert list(rows[0]) == ["Lipinski_pass", "lipinski_pass_2", "_3d"]


def test_the_tools_appear_only_once_a_connection_is_saved(saved, monkeypatch):
    assert tools.is_tool_locally_available("push_to_destination")
    assert tools.is_tool_locally_available("pull_from_source")
    monkeypatch.setenv("NOVO_CONNECTORS_PATH", str(saved / "missing.json"))
    assert not tools.is_tool_locally_available("push_to_destination")
    monkeypatch.setenv("NOVO_CONNECTORS_PATH", str(saved / "connectors.json"))
    monkeypatch.setenv("NOVO_AUTH", "hosted")
    assert not local_bridge.available()


@pytest.fixture
def executor(saved, monkeypatch):
    monkeypatch.delenv("FUNNEL_BACKEND_URL", raising=False)
    ex = MCPToolExecutor(service_urls={}, internal_api_key="local")
    ex.bridge_url = ""
    ex.spine = Spine(auth=LocalAuthGate(), meter=NoopMeter(), audit=FileAuditSink(str(saved / "audit.jsonl")))
    return ex


def _events(path, event):
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r["payload"] for r in lines if r.get("event") == event]


async def _call(ex, tool, args):
    return await ex.execute(tool, args, org_id="local", user_id="local")


async def test_push_exports_rows_and_records_the_export(executor, saved):
    listed = await _call(executor, "push_to_destination", {"action": "list_connections"})
    assert listed.success and listed.data["connections"][0]["display_name"] == "Lab warehouse"

    rows = [{"smiles": "CCO", "qed": 0.41}, {"smiles": "CCN", "qed": 0.38}]
    out = await _call(executor, "push_to_destination",
                      {"action": "export", "data": rows, "source_tool": "screen_library", "target": "screened"})
    assert out.success, out.error
    assert out.data["rows_written"] == 2 and out.data["target_location"] == "SQLITE:main.screened"

    rec = _events(saved / "audit.jsonl", "data_export")[-1]
    assert (rec["rows_written"], rec["target_location"], rec["source_tool"]) == (2, "SQLITE:main.screened", "screen_library")

    pulled = await _call(executor, "pull_from_source", {"action": "pull", "connection_id": "conn_1", "table": "screened"})
    assert pulled.success, pulled.error
    assert sorted(r["smiles"] for r in pulled.data["rows"]) == ["CCN", "CCO"]


async def test_push_sends_a_runs_audit_trail(executor, saved):
    run = "fnl_export_1"
    await _call(executor, "calculate_properties", {"smiles": "CCO", "funnel_id": run})
    await _call(executor, "save_funnel_stage", {"funnel_id": run, "stage_name": "review", "human_decision": "keep"})
    out = await _call(executor, "push_to_destination", {"action": "export", "funnel_id": run})
    assert out.success, out.error
    assert out.data["rows_written"] == 2 and out.data["target_location"].endswith("novomcp_run_audit")

    con = sqlite3.connect(saved / "w.db")
    got = con.execute('SELECT event_type, funnel_id, _export_id FROM novomcp_run_audit ORDER BY timestamp').fetchall()
    assert [g[0] for g in got] == ["exploration", "checkpoint"] and {g[1] for g in got} == {run}
    assert all(g[2].startswith("exp_") for g in got)


async def test_pull_pipeline_processes_rows_and_pushes_them_back(executor, saved):
    con = sqlite3.connect(saved / "w.db")
    con.execute("CREATE TABLE library (id INTEGER, smiles TEXT)")
    con.executemany("INSERT INTO library VALUES (?, ?)", [(1, "CCO"), (2, "c1ccccc1O")])
    con.commit()

    est = await _call(executor, "pull_from_source", {
        "action": "estimate_pipeline", "connection_id": "conn_1", "table": "library",
        "processing_tools": ["calculate_properties"], "destination_connection_id": "conn_1",
        "destination_table": "library_enriched",
    })
    assert est.success, est.error
    run = await _call(executor, "pull_from_source", {
        "action": "execute_pipeline", "confirmation_token": est.data["confirmation_token"],
        "destination_connection_id": "conn_1", "destination_table": "library_enriched",
    })
    assert run.success, run.error
    assert run.data["rows_pulled"] == 2 and run.data["destination"]["rows_pushed"] == 2

    pushed = con.execute("SELECT COUNT(*) FROM library_enriched").fetchone()[0]
    assert pushed == 2
    rec = _events(saved / "audit.jsonl", "data_pipeline")[-1]
    assert (rec["status"], rec["rows_pulled"], rec["rows_pushed"]) == ("completed", 2, 2)


async def test_multirow_inserts_batch_rows_and_fall_back_per_row(tmp_path):
    from dataclasses import replace

    class Multi(SqliteWarehouse):
        dialect = replace(SQLITE, multirow_params=6)  # 2 columns → 3 rows per statement

    wh = Multi({"path": str(tmp_path / "w.db")}, {})
    statements = []
    real = wh._get_connection()
    real.set_trace_callback(statements.append)
    out = await wh.write_data("t", [{"a": i, "b": str(i)} for i in range(7)])
    assert out.success and out.rows_written == 7
    inserts = [s for s in statements if s.startswith("INSERT")]
    assert len(inserts) == 3  # 3 + 3 + 1 rows
    assert await wh.count_rows("t") == 7
    await wh.close()


def test_multirow_insert_sql():
    stmt = sql.insert_sql(sql.DATABRICKS, ["c", "s", "t"], ["a", "b"], rows=2)
    assert stmt == "INSERT INTO `c`.`s`.`t` (`a`, `b`) VALUES (?, ?), (?, ?)"
