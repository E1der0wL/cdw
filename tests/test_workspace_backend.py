"""Acceptance tests for durable query tabs, memory SQL and complete exports."""

import csv
import io
import json
import subprocess
import sys
import textwrap
import threading
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

from cdw.application.examples import demo_preset
from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.contracts import CdwError, ErrorCode, StorageMode, StorageTarget


@pytest.fixture
def service(tmp_path):
    service = ApplicationService(build_config(tmp_path))
    yield service
    service.close()


def collected(service, *, memory=False, name=None, database=None, payload='[{"id":1}]'):
    preset = replace(demo_preset(service.config, memory=memory), id=str(uuid4()), name=name or str(uuid4()),
                     arguments=("-c", f"print({payload!r})"))
    if database:
        preset = replace(preset, storage=StorageTarget(StorageMode.FILE, str(database)))
    service.save_preset(preset)
    service.collect(preset.id)
    return preset


def test_full_snapshot_exceeds_cli_preview_and_is_not_reexecuted(service, tmp_path):
    preset = collected(service)
    sql = "SELECT i AS id, random() AS sample FROM range(750) t(i) ORDER BY i"
    assert service.query(preset.id, sql).truncated
    snapshot = service.query_workspace(preset.id, sql)
    assert snapshot.title == "range"
    assert len(snapshot.result.rows) == 750
    assert snapshot.result.truncated is False
    filename = service.export_result(snapshot.id, tmp_path / "all.duckdb", "duckdb")
    with duckdb.connect(filename, read_only=True) as exported:
        assert tuple(exported.execute("SELECT * FROM result").fetchall()) == snapshot.result.rows
    assert service.copy_result(snapshot.id, "text").splitlines()[0] == "id\tsample"
    assert len(service.copy_result(snapshot.id, "text").splitlines()) == 751


def test_save_and_exports_use_snapshot_after_source_changes(service, tmp_path):
    preset = collected(service, payload='[{"id":1,"name":"<old>&"},{"id":2,"name":"second"}]')
    snapshot = service.query_workspace(preset.id, "SELECT * FROM source.host_inventory ORDER BY id")
    service.save_preset(replace(preset, arguments=("-c", "print('[{\"id\":99,\"name\":\"new\"}]')")))
    service.collect(preset.id)
    saved = service.save_result_table(snapshot.id, "retained", (1, 0))
    assert saved.name == "memory.retained"
    assert saved.display_name == "retained"
    assert service.query_workspace(preset.id, "SELECT * FROM memory.retained").result.rows == ((2, "second"), (1, "<old>&"))
    assert service.query_workspace(None, "SELECT count(*) FROM memory.retained").result.rows == ((2,),)
    assert service.copy_result(snapshot.id, "text", (0,)) == "id\tname\n1\t<old>&\n"
    markup = service.copy_result(snapshot.id, "html", (0,))
    assert "&lt;old&gt;&amp;" in markup and "second" not in markup
    filename = service.export_result(snapshot.id, tmp_path / "rows.csv", "csv", "|", (1, 0))
    assert list(csv.reader(io.StringIO(Path(filename).read_text(encoding="utf-8")), delimiter="|")) == [
        ["id", "name"], ["2", "second"], ["1", "<old>&"]]
    filename = service.export_result(snapshot.id, tmp_path / "rows.json", "json", row_indices=(1, 0))
    assert json.loads(Path(filename).read_text(encoding="utf-8")) == [{"id": 2, "name": "second"}, {"id": 1, "name": "<old>&"}]


def test_native_types_duplicate_names_and_rowid_survive_memory_and_duckdb(service, tmp_path):
    snapshot = service.query_workspace(None, """SELECT
        17::BIGINT AS rowid, 1 AS same, 2 AS same,
        1234567890123.4500::DECIMAL(20,4) AS amount,
        DATE '2026-11-14' AS day, TIMESTAMP '2026-11-14 12:30:00' AS moment,
        NULL::INTEGER AS missing, from_hex('00ff41') AS payload,
        [1,2,NULL] AS values, {'city':'서울','count':3} AS details""")
    assert len(set(snapshot.result.columns)) == len(snapshot.result.columns)
    assert snapshot.result.rows[0][3:7] == (Decimal("1234567890123.4500"), date(2026, 11, 14), datetime(2026, 11, 14, 12, 30), None)
    saved = service.save_result_table(snapshot.id, "typed", (0,))
    again = service.query_workspace(None, "SELECT * FROM memory.typed")
    assert again.result.types == snapshot.result.types
    assert again.result.rows == snapshot.result.rows
    assert saved.columns == tuple(zip(snapshot.result.columns, snapshot.result.types))
    filename = service.export_result(snapshot.id, tmp_path / "typed.duckdb", "duckdb", row_indices=(0,))
    with duckdb.connect(filename, read_only=True) as exported:
        cursor = exported.execute("SELECT * FROM result")
        assert tuple(str(column[1]) for column in cursor.description) == snapshot.result.types
        assert tuple(cursor.fetchall()) == snapshot.result.rows
    filename = service.export_result(snapshot.id, tmp_path / "typed.json", "json")
    row = json.loads(Path(filename).read_text(encoding="utf-8"))[0]
    assert row["amount"] == "1234567890123.4500"
    assert row["day"] == "2026-11-14"
    assert row["payload"] == {"$binary": "AP9B"}


def test_empty_typed_results_and_empty_filter_export_keep_schema(service, tmp_path):
    snapshot = service.query_workspace(None, "SELECT 1::INTEGER AS id, DATE '2026-01-01' AS day WHERE false")
    assert snapshot.result.rows == ()
    service.save_result_table(snapshot.id, "empty")
    result = service.query_workspace(None, "SELECT * FROM memory.empty").result
    assert result.columns == ("id", "day") and result.types == ("INTEGER", "DATE") and result.rows == ()
    source = service.query_workspace(None, "SELECT 1::INTEGER AS id, DATE '2026-01-01' AS day")
    filename = service.export_result(source.id, tmp_path / "empty.duckdb", "duckdb", row_indices=())
    with duckdb.connect(filename) as exported:
        assert exported.execute("SELECT * FROM result").fetchall() == []
        assert [row[1] for row in exported.execute("DESCRIBE result").fetchall()] == ["INTEGER", "DATE"]


def test_memory_name_collision_and_release_preserve_saved_table(service):
    snapshot = service.query_workspace(None, "SELECT 1 id")
    service.save_result_table(snapshot.id, "saved")
    with pytest.raises(CdwError) as collision:
        service.save_result_table(snapshot.id, "saved")
    assert collision.value.code is ErrorCode.PRESET_CONFLICT
    with pytest.raises(CdwError):
        service.save_result_table(snapshot.id, 'bad; DROP TABLE memory.saved')
    service.release_result(snapshot.id)
    service.release_result(snapshot.id)
    with pytest.raises(CdwError) as gone:
        service.copy_result(snapshot.id, "text")
    assert gone.value.code is ErrorCode.NOT_FOUND
    assert service.query_workspace(None, "SELECT * FROM memory.saved").result.rows == ((1,),)


def test_catalog_lists_all_files_memory_and_cross_file_queries(service, tmp_path):
    first = collected(service, database=tmp_path / "one.duckdb", payload='[{"id":1}]')
    second = collected(service, database=tmp_path / "two.duckdb", payload='[{"id":2}]')
    memory = collected(service, memory=True, payload='[{"id":3}]')
    entries = service.workspace_tables()
    assert len(entries) == 3
    assert all(entry.display_name == "host_inventory" for entry in entries)
    by_preset = {entry.preset_id: entry for entry in entries}
    assert by_preset[first.id].storage_mode is StorageMode.FILE
    assert by_preset[memory.id].storage_mode is StorageMode.MEMORY
    assert all(entry.updated_at for entry in entries)
    sql = f"SELECT a.id,b.id,c.id FROM {by_preset[first.id].name} a CROSS JOIN {by_preset[second.id].name} b CROSS JOIN {by_preset[memory.id].name} c"
    assert service.query_workspace(None, sql).result.rows == ((1, 2, 3),)
    # CLI context must remain isolated even after catalog and cross-file operations.
    assert service.query(first.id, "SELECT * FROM source.host_inventory").rows == ((1,),)
    assert service.query(second.id, "SELECT * FROM source.host_inventory").rows == ((2,),)


def test_catalog_discovers_existing_non_source_tables_and_skips_missing_files(service, tmp_path):
    existing = tmp_path / "existing.duckdb"
    with duckdb.connect(str(existing)) as database:
        database.execute('CREATE TABLE "odd name" AS SELECT 7 AS value')
    preset = service.save_preset(replace(demo_preset(service.config), name="Existing", storage=StorageTarget(StorageMode.FILE, str(existing))))
    service.save_preset(replace(demo_preset(service.config), id=str(uuid4()), name="Missing", storage=StorageTarget(StorageMode.FILE, str(tmp_path / "missing.duckdb"))))
    entries = service.workspace_tables()
    assert len(entries) == 1
    assert entries[0].name.endswith('.main."odd name"')
    assert entries[0].display_name == "odd name"
    assert service.query_workspace(preset.id, f"SELECT * FROM {entries[0].name}").result.rows == ((7,),)
    assert not (tmp_path / "missing.duckdb").exists()


@pytest.mark.parametrize("sql,title", [
    ("SELECT 'from fake' x", "결과"),
    ("WITH example AS (SELECT 1 id) SELECT * FROM example", "example"),
    ('SELECT * FROM (SELECT 1 id) AS nested', "결과"),
    ("SELECT * FROM range(2) a JOIN range(2) b ON a.range=b.range", "range"),
])
def test_tab_titles_use_sql_parser_not_text_matching(service, sql, title):
    assert service.query_workspace(None, sql).title == title


def test_failed_exports_leave_existing_target_and_protect_source_and_metadata(service, tmp_path):
    preset = collected(service)
    snapshot = service.query_workspace(preset.id, "SELECT * FROM source.host_inventory")
    target = tmp_path / "existing.csv"
    target.write_text("keep me", encoding="utf-8")
    for delimiter in ("", "||", "\n", '"'):
        with pytest.raises(CdwError):
            service.export_result(snapshot.id, target, "csv", delimiter)
        assert target.read_text(encoding="utf-8") == "keep me"
    for path in (preset.storage.key, service.config.metadata_database):
        with pytest.raises(CdwError):
            service.export_result(snapshot.id, path, "duckdb")
    assert service.query(preset.id, "SELECT count(*) FROM source.host_inventory").rows == ((1,),)
    assert service.get_preset(preset.id) == preset
    assert not list(tmp_path.glob(".cdw-export-*"))


@pytest.mark.parametrize("indices", [(-1,), (1,), (True,), (0.5,)])
def test_invalid_row_selections_do_not_create_memory_tables(service, indices):
    snapshot = service.query_workspace(None, "SELECT 1 id")
    with pytest.raises(CdwError):
        service.save_result_table(snapshot.id, "invalid", indices)
    assert service.workspace_tables() == []


def test_workspace_query_cancellation_keeps_engine_usable(service):
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(CdwError) as stopped:
        service.query_workspace(None, "SELECT 1", cancellation)
    assert stopped.value.code is ErrorCode.CANCELLED
    assert service.query_workspace(None, "SELECT 2").result.rows == ((2,),)


def test_running_workspace_query_is_interruptible_and_can_be_retried(service):
    service.query_workspace(None, "SELECT 1")
    cancellation = threading.Event()

    def interrupt():
        cancellation.set()
        service.interrupt_query()

    timer = threading.Timer(0.2, interrupt)
    timer.start()
    try:
        with pytest.raises(CdwError) as stopped:
            service.query_workspace(None, "SELECT sum(a.i*b.i) FROM range(1000000) a(i), range(1000000) b(i)", cancellation)
        assert stopped.value.code is ErrorCode.CANCELLED
    finally:
        timer.cancel()
        timer.join()
    assert service.query_workspace(None, "SELECT 2").result.rows == ((2,),)
    assert service.workspace_tables() == []


def test_atomic_export_preserves_existing_output_on_replace_failure(service, tmp_path, monkeypatch):
    import cdw.infrastructure.duckdb_store as storage

    snapshot = service.query_workspace(None, "SELECT 1 AS id")
    output = tmp_path / "existing.csv"
    output.write_text("previous", encoding="utf-8")

    def fail_replace(source, destination):
        raise PermissionError("output is in use")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(CdwError) as failed:
        service.export_result(snapshot.id, output, "csv")
    assert failed.value.code is ErrorCode.STORAGE_FAILED
    assert output.read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob(".cdw-export-*"))


def test_query_does_not_create_staging_view_inside_source_file(service, tmp_path):
    preset = collected(service)
    service.query_workspace(preset.id, "SELECT * FROM source.host_inventory; -- trailing comment")
    # The catalog contents are observable through SQL; query staging must remain
    # temporary even while the SELECT is running.
    result = service.query_workspace(preset.id, """SELECT count(*) AS persistent_staging
        FROM duckdb_views() WHERE view_name LIKE '_cdw_query_%' AND NOT temporary""")
    assert result.result.rows == ((0,),)


def test_saved_memory_tables_disappear_after_restart_but_file_catalog_remains(tmp_path):
    config = build_config(tmp_path)
    service = ApplicationService(config)
    preset = collected(service)
    snapshot = service.query_workspace(preset.id, "SELECT * FROM source.host_inventory")
    service.save_result_table(snapshot.id, "session_only")
    service.close()
    reopened = ApplicationService(config)
    try:
        assert len(reopened.workspace_tables()) == 1
        assert reopened.workspace_tables()[0].updated_at
        with pytest.raises(CdwError):
            reopened.query_workspace(preset.id, "SELECT * FROM memory.session_only")
    finally:
        reopened.close()


def test_fresh_qt_worker_exports_large_selection_before_any_collection(tmp_path):
    # An isolated interpreter is essential: previous Polars/Arrow collections
    # otherwise warm optional imports and conceal the first-export regression.
    script = textwrap.dedent("""
        import csv
        import json
        import sys
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        import duckdb
        from qtpy.QtTest import QTest
        from qtpy.QtWidgets import QApplication
        from cdw.application.service import ApplicationService
        from cdw.config import build_config

        directory = Path(sys.argv[1])
        app = QApplication([])
        service = ApplicationService(build_config(directory))

        def work():
            try:
                snapshot = service.query_workspace(None,
                    "SELECT n, 'server;' || n AS label FROM range(620) t(n)")
                selected = tuple(range(619, 1, -1))
                for format in ('csv', 'json', 'duckdb'):
                    service.export_result(snapshot.id, directory / ('rows.' + format),
                                          format, ';', selected)
                service.save_result_table(snapshot.id, 'filtered', selected)
                assert service.query_workspace(None, 'SELECT count(*) FROM memory.filtered').result.rows == ((618,),)
            finally:
                service.close()

        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(work)
            while not pending.done():
                app.processEvents()
                QTest.qWait(10)
            pending.result()
        with (directory / 'rows.csv').open(encoding='utf-8', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=';'))
            assert len(rows) == 619 and rows[1] == ['619', 'server;619']
        rows = json.loads((directory / 'rows.json').read_text(encoding='utf-8'))
        assert len(rows) == 618 and rows[-1] == {'n': 2, 'label': 'server;2'}
        with duckdb.connect(str(directory / 'rows.duckdb')) as database:
            rows = database.execute('SELECT * FROM result').fetchall()
            assert len(rows) == 618 and rows[0] == (619, 'server;619') and rows[-1][0] == 2
    """)
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True,
                               text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
