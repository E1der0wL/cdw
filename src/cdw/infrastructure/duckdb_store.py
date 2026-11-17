"""DuckDB SQL, registration, transaction and error interpretation live here."""

from __future__ import annotations

from cdw.i18n import tr

from cdw.infrastructure.duckdb_types import column_kind

import threading
import time
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import polars as pl

from cdw.domain.contracts import (
    CdwError, ErrorCode, Preset, QueryPolicy, QueryResult, SOURCE_SCHEMA,
    StorageMode, StorageTarget, TableInfo, WriteMode, TABLE_NAME_PATTERN, VALIDATION_MESSAGES,
)
from cdw.domain.workspace import CopyFormat, DEFAULT_EXPORT_DELIMITER, ExportFormat, QuerySnapshot, WorkspaceTable
from cdw.infrastructure.result_exports import EXPORT_ENCODING, TEXT_DELIMITER, csv_text, html_text, json_text, validate_delimiter

INTERNAL_SCHEMA = "cdw_internal"
COMMIT_TABLE = "cdw_internal.commits"
SIMPLE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class _SnapshotState:
    snapshot: QuerySnapshot
    table: str
    index_column: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _qualified(*parts: str) -> str:
    return ".".join(quote_identifier(part) for part in parts)


def _display_qualified(*parts: str) -> str:
    return ".".join(part if SIMPLE_IDENTIFIER.fullmatch(part) else quote_identifier(part) for part in parts)


class DuckDBStore:

    INPUT_RELATION = "_cdw_input_frame"
    ALLOWED_QUERY_TYPES = frozenset({"SELECT"})
    INITIALIZE_STATEMENTS = (
        f'CREATE SCHEMA IF NOT EXISTS "{SOURCE_SCHEMA}"',
        f'CREATE SCHEMA IF NOT EXISTS "{INTERNAL_SCHEMA}"',
        f"CREATE TABLE IF NOT EXISTS {COMMIT_TABLE} (run_id VARCHAR PRIMARY KEY, inserted_rows BIGINT, total_rows BIGINT)",
    )
    BEGIN_SQL = "BEGIN TRANSACTION"
    COMMIT_SQL = "COMMIT"
    ROLLBACK_SQL = "ROLLBACK"
    EXISTS_SQL = "SELECT count(*) FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema=? AND table_name=?"
    DESCRIBE_SQL = "DESCRIBE SELECT * FROM {table}"
    REPLACE_SQL = "CREATE OR REPLACE TABLE {table} AS SELECT * FROM {input}"
    APPEND_SQL = "INSERT INTO {table} SELECT * FROM {input}"
    COUNT_SQL = "SELECT count(*) FROM {table}"
    MARK_COMMIT_SQL = f"INSERT INTO {COMMIT_TABLE} VALUES (?, ?, ?)"
    FIND_COMMIT_SQL = f"SELECT inserted_rows,total_rows FROM {COMMIT_TABLE} WHERE run_id=?"
    TABLE_LIST_SQL = "SELECT table_name FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema=? ORDER BY table_name"
    THREAD_ERROR_MESSAGE = tr('database.thread_error')
    READ_DATABASE_MISSING_MESSAGE = tr('infrastructure.duckdb_store.read_database_missing_message')
    QUERY_EXTRA_ROWS = 1
    CONNECTION_OPTIONS = {"autoinstall_known_extensions": "false", "autoload_known_extensions": "false"}
    MEMORY_CATALOG = "memory"
    MAIN_SCHEMA = "main"
    RESULT_SCHEMA = "cdw_results"
    CATALOG_PREFIX = "db_"
    CATALOG_HASH_LENGTH = 8
    CATALOG_STEM_LENGTH = 24
    CATALOG_SANITIZER = re.compile(r"[^a-z0-9_]")
    WORKSPACE_TABLE_SQL = """SELECT table_catalog, table_schema, table_name FROM information_schema.tables
    WHERE table_schema NOT IN (?, ?) AND table_catalog != 'temp' ORDER BY table_catalog,table_schema,table_name"""
    SNAPSHOT_TABLE_PREFIX = "result_"
    SNAPSHOT_INDEX_PREFIX = "_cdw_row_"
    QUERY_VIEW_PREFIX = "_cdw_query_"
    EXPORT_CATALOG_PREFIX = "_cdw_export_"
    EXPORT_TABLE_NAME = "result"
    EXPORT_TEMP_PREFIX = ".cdw-export-"
    NO_SOURCE_TITLE = tr('common.result')
    INVALID_SELECTION = tr('infrastructure.duckdb_store.invalid_selection')
    MISSING_RESULT = tr('infrastructure.duckdb_store.missing_result')
    MEMORY_NAME_CONFLICT = tr('infrastructure.duckdb_store.memory_name_conflict')
    PROTECTED_EXPORT_PATH = tr('infrastructure.duckdb_store.protected_export_path')
    INVALID_EXPORT_FORMAT = tr('infrastructure.duckdb_store.invalid_export_format')
    READ_ONLY_MESSAGE = tr('infrastructure.duckdb_store.read_only_message')

    def __init__(self) -> None:
        self._engine: duckdb.DuckDBPyConnection | None = None
        self._catalogs: dict[str, str] = {":memory:": DuckDBStore.MEMORY_CATALOG}
        self._readonly: set[str] = set()
        self._snapshots: dict[str, _SnapshotState] = {}
        self._updates: dict[tuple[str, str, str], str] = {}
        self._owner: int | None = None

    def _check_thread(self) -> None:
        owner = threading.get_ident()
        if self._owner is None:
            self._owner = owner
        if self._owner != owner:
            raise RuntimeError(DuckDBStore.THREAD_ERROR_MESSAGE)

    def _connection(self, target: StorageTarget, *, create: bool = True) -> duckdb.DuckDBPyConnection:
        self._check_thread()
        if self._engine is None:
            self._engine = duckdb.connect(":memory:", config=DuckDBStore.CONNECTION_OPTIONS)
            for statement in DuckDBStore.INITIALIZE_STATEMENTS:
                self._engine.execute(statement)
            self._engine.execute(f"CREATE SCHEMA {quote_identifier(DuckDBStore.RESULT_SCHEMA)}")
        connection = self._engine
        if target.key not in self._catalogs:
            path = Path(target.key)
            if not create and not path.is_file():
                raise CdwError(ErrorCode.NOT_FOUND, DuckDBStore.READ_DATABASE_MISSING_MESSAGE)
            if create:
                path.parent.mkdir(parents=True, exist_ok=True)
            stem = DuckDBStore.CATALOG_SANITIZER.sub("_", path.stem.lower())[:DuckDBStore.CATALOG_STEM_LENGTH]
            digest = hashlib.sha256(os.path.normcase(target.key).encode(EXPORT_ENCODING)).hexdigest()[:DuckDBStore.CATALOG_HASH_LENGTH]
            catalog = f"{DuckDBStore.CATALOG_PREFIX}{stem}_{digest}"
            attach = f"ATTACH {_quote_literal(target.key)} AS {quote_identifier(catalog)}"
            try:
                connection.execute(attach)
            except duckdb.Error:
                if create:
                    raise
                connection.execute(attach + " (READ_ONLY)")
                self._readonly.add(target.key)
            self._catalogs[target.key] = catalog
        connection.execute(f"USE {quote_identifier(self._catalogs[target.key])}")
        if create:
            if target.key in self._readonly:
                raise CdwError(ErrorCode.STORAGE_FAILED, DuckDBStore.READ_ONLY_MESSAGE)
            for statement in DuckDBStore.INITIALIZE_STATEMENTS:
                connection.execute(statement)
        return connection

    def write(self, preset: Preset, frame: pl.DataFrame, run_id: str) -> tuple[int, int]:
        table = f"{quote_identifier(SOURCE_SCHEMA)}.{quote_identifier(preset.table_name)}"
        try:
            connection = self._connection(preset.storage)
            connection.register(DuckDBStore.INPUT_RELATION, frame.to_arrow())
            try:
                connection.execute(DuckDBStore.BEGIN_SQL)
                try:
                    exists = connection.execute(DuckDBStore.EXISTS_SQL, [SOURCE_SCHEMA, preset.table_name]).fetchone()[0]
                    if preset.write_mode is WriteMode.APPEND and exists:
                        old_schema = connection.execute(DuckDBStore.DESCRIBE_SQL.format(table=table)).fetchall()
                        new_schema = connection.execute(DuckDBStore.DESCRIBE_SQL.format(table=DuckDBStore.INPUT_RELATION)).fetchall()
                        if [(r[0], r[1]) for r in old_schema] != [(r[0], r[1]) for r in new_schema]:
                            raise CdwError(ErrorCode.SCHEMA_MISMATCH)
                        connection.execute(DuckDBStore.APPEND_SQL.format(table=table, input=DuckDBStore.INPUT_RELATION))
                    else:
                        connection.execute(DuckDBStore.REPLACE_SQL.format(table=table, input=DuckDBStore.INPUT_RELATION))
                    total = connection.execute(DuckDBStore.COUNT_SQL.format(table=table)).fetchone()[0]
                    connection.execute(DuckDBStore.MARK_COMMIT_SQL, [run_id, frame.height, total])
                    connection.execute(DuckDBStore.COMMIT_SQL)
                    self._updates[(self._catalogs[preset.storage.key], SOURCE_SCHEMA, preset.table_name)] = _utc_now()
                except BaseException:
                    connection.execute(DuckDBStore.ROLLBACK_SQL)
                    raise
            finally:
                connection.unregister(DuckDBStore.INPUT_RELATION)
            return frame.height, total
        except duckdb.Error as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, str(exc)) from exc

    def query(self, target: StorageTarget, sql: str, policy: QueryPolicy,
              cancel: threading.Event | None = None) -> QueryResult:
        started = time.monotonic()
        try:
            connection = self._connection(target, create=False)
            statements = connection.extract_statements(sql)
            if len(statements) != 1 or statements[0].type.name not in DuckDBStore.ALLOWED_QUERY_TYPES:
                raise CdwError(ErrorCode.INVALID_QUERY)
            if cancel is not None and cancel.is_set():
                raise CdwError(ErrorCode.CANCELLED)
            cursor = connection.execute(sql)
            description = cursor.description
            rows = cursor.fetchmany(policy.preview_rows + DuckDBStore.QUERY_EXTRA_ROWS)
            return QueryResult(
                columns=tuple(item[0] for item in description),
                types=tuple(str(item[1]) for item in description),
                kinds=tuple(column_kind(str(item[1])) for item in description),
                rows=tuple(tuple(row) for row in rows[:policy.preview_rows]),
                truncated=len(rows) > policy.preview_rows, elapsed_seconds=time.monotonic() - started,
            )
        except duckdb.InterruptException as exc:
            raise CdwError(ErrorCode.CANCELLED) from exc
        except duckdb.Error as exc:
            raise CdwError(ErrorCode.QUERY_FAILED, str(exc)) from exc

    def tables(self, target: StorageTarget) -> list[TableInfo]:
        try:
            connection = self._connection(target, create=False)
            names = connection.execute(DuckDBStore.TABLE_LIST_SQL, [SOURCE_SCHEMA]).fetchall()
            result = []
            for (name,) in names:
                table = f"{quote_identifier(SOURCE_SCHEMA)}.{quote_identifier(name)}"
                columns = connection.execute(DuckDBStore.DESCRIBE_SQL.format(table=table)).fetchall()
                result.append(TableInfo(f"{SOURCE_SCHEMA}.{name}", tuple((row[0], row[1]) for row in columns)))
            return result
        except duckdb.Error as exc:
            raise CdwError(ErrorCode.QUERY_FAILED, str(exc)) from exc

    def _connect_known(self, presets: list[Preset]) -> None:
        self._connection(StorageTarget(StorageMode.MEMORY, None), create=False)
        for preset in presets:
            if preset.storage.mode is StorageMode.FILE and Path(preset.storage.key).is_file():
                try:
                    self._connection(preset.storage, create=False)
                except (CdwError, duckdb.Error):
                    # A stale/inaccessible registered file must not hide other tables.
                    continue

    def workspace_tables(self, presets: list[Preset], persisted_updates: dict[tuple[str, str], str] | None = None) -> list[WorkspaceTable]:
        self._connect_known(presets)
        connection = self._engine
        assert connection is not None
        reverse = {catalog: key for key, catalog in self._catalogs.items()}
        preset_map = {(preset.storage.key, SOURCE_SCHEMA, preset.table_name): preset.id for preset in presets}
        result = []
        names = connection.execute(DuckDBStore.WORKSPACE_TABLE_SQL, [INTERNAL_SCHEMA, DuckDBStore.RESULT_SCHEMA]).fetchall()
        for catalog, schema, name in names:
            if catalog not in reverse:
                continue
            key = reverse[catalog]
            mode = StorageMode.MEMORY if key == ":memory:" else StorageMode.FILE
            if mode is StorageMode.FILE and not Path(key).is_file():
                continue
            qualified = _qualified(catalog, schema, name)
            try:
                columns = connection.execute(DuckDBStore.DESCRIBE_SQL.format(table=qualified)).fetchall()
            except duckdb.Error:
                continue
            updated = self._updates.get((catalog, schema, name))
            if updated is None and schema == SOURCE_SCHEMA:
                updated = (persisted_updates or {}).get((key, name))
            if updated is None and mode is StorageMode.FILE:
                try:
                    updated = datetime.fromtimestamp(Path(key).stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    pass
            sql_name = (_display_qualified(DuckDBStore.MEMORY_CATALOG, name) if catalog == DuckDBStore.MEMORY_CATALOG and schema == DuckDBStore.MAIN_SCHEMA
                        else _display_qualified(catalog, schema, name))
            result.append(WorkspaceTable(sql_name, mode, updated,
                                         tuple((row[0], row[1]) for row in columns), key,
                                         name, preset_map.get((key, schema, name))))
        return result

    @staticmethod
    def _query_title(connection, sql: str) -> str:
        def source_name(node):
            if not isinstance(node, dict):
                return None
            if node.get("type") == "BASE_TABLE":
                return node.get("table_name")
            if node.get("type") == "TABLE_FUNCTION":
                return node.get("function", {}).get("function_name")
            # Outer FROM wins over sources inside CTE definitions.
            for key in ("from_table", "left", "subquery", "node", "query"):
                found = source_name(node.get(key))
                if found:
                    return found
            return None
        try:
            parsed = json.loads(connection.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
            return source_name(parsed["statements"][0]["node"]) or DuckDBStore.NO_SOURCE_TITLE
        except (duckdb.Error, ValueError, KeyError, IndexError, TypeError):
            return DuckDBStore.NO_SOURCE_TITLE

    def query_workspace(self, target: StorageTarget | None, sql: str, presets: list[Preset],
                        cancel: threading.Event | None = None) -> QuerySnapshot:
        started = time.monotonic()
        snapshot_id = uuid4().hex
        table = _qualified(DuckDBStore.MEMORY_CATALOG, DuckDBStore.RESULT_SCHEMA, DuckDBStore.SNAPSHOT_TABLE_PREFIX + snapshot_id)
        view = DuckDBStore.QUERY_VIEW_PREFIX + snapshot_id
        created = False
        try:
            self._connect_known(presets)
            # Missing/inaccessible selected files must not block saved-memory queries.
            context = target if target is not None and target.key in self._catalogs else StorageTarget(StorageMode.MEMORY, None)
            connection = self._connection(context, create=False)
            statements = connection.extract_statements(sql)
            if len(statements) != 1 or statements[0].type.name not in DuckDBStore.ALLOWED_QUERY_TYPES:
                raise CdwError(ErrorCode.INVALID_QUERY)
            if cancel is not None and cancel.is_set():
                raise CdwError(ErrorCode.CANCELLED)
            title = self._query_title(connection, sql)
            relation = connection.sql(sql)
            index_column = DuckDBStore.SNAPSHOT_INDEX_PREFIX + snapshot_id
            while index_column.lower() in {name.lower() for name in relation.columns}:
                index_column += "_"
            # Relation.create_view() persists into the selected catalog. Stage in
            # temp instead so a SELECT never modifies an attached source file.
            connection.execute(f"CREATE TEMP VIEW {quote_identifier(view)} AS {relation.sql_query()}")
            try:
                connection.execute(f"CREATE TABLE {table} AS SELECT row_number() OVER () - 1 AS {quote_identifier(index_column)}, * FROM {quote_identifier(view)}")
                created = True
            finally:
                connection.execute(f"DROP VIEW IF EXISTS {quote_identifier(view)}")
            cursor = connection.execute(f"SELECT * EXCLUDE ({quote_identifier(index_column)}) FROM {table} ORDER BY {quote_identifier(index_column)}")
            description = cursor.description
            rows = tuple(tuple(row) for row in cursor.fetchall())
            if cancel is not None and cancel.is_set():
                raise CdwError(ErrorCode.CANCELLED)
            result = QueryResult(tuple(item[0] for item in description), tuple(str(item[1]) for item in description),
                                 rows, False, time.monotonic() - started, tuple(column_kind(str(item[1])) for item in description))
            snapshot = QuerySnapshot(snapshot_id, title, result, _utc_now())
            self._snapshots[snapshot_id] = _SnapshotState(snapshot, table, index_column)
            return snapshot
        except duckdb.InterruptException as exc:
            raise CdwError(ErrorCode.CANCELLED) from exc
        except duckdb.Error as exc:
            raise CdwError(ErrorCode.QUERY_FAILED, str(exc)) from exc
        finally:
            if created and snapshot_id not in self._snapshots and self._engine is not None:
                self._engine.execute(f"DROP TABLE IF EXISTS {table}")

    def _selection(self, snapshot_id: str, row_indices: tuple[int, ...] | None = None):
        self._check_thread()
        state = self._snapshots.get(snapshot_id)
        if state is None:
            raise CdwError(ErrorCode.NOT_FOUND, DuckDBStore.MISSING_RESULT)
        columns = ", ".join("s." + quote_identifier(name) for name in state.snapshot.result.columns)
        index = quote_identifier(state.index_column)
        if row_indices is None:
            return state, f"SELECT {columns} FROM {state.table} s ORDER BY s.{index}", []
        if not isinstance(row_indices, (tuple, list)) or any(type(index) is not int or not 0 <= index < len(state.snapshot.result.rows) for index in row_indices):
            raise CdwError(ErrorCode.INVALID_QUERY, DuckDBStore.INVALID_SELECTION)
        sql = (f"SELECT {columns} FROM UNNEST(?::BIGINT[]) WITH ORDINALITY AS chosen(row_index, position) "
               f"JOIN {state.table} s ON s.{index}=chosen.row_index ORDER BY chosen.position")
        # Pass one scalar rather than a nested Python list. DuckDB's Python
        # sequence adapter probes optional array modules for its elements;
        # that can stall a fresh GUI worker before any Arrow-based collection.
        # Native BIGINT[] parsing keeps selection/order independent of imports.
        return state, sql, [json.dumps(row_indices)]

    def result_snapshot(self, snapshot_id: str, row_indices: tuple[int, ...] | None = None) -> QueryResult:
        state, sql, parameters = self._selection(snapshot_id, row_indices)
        assert self._engine is not None
        rows = self._engine.execute(sql, parameters).fetchall()
        original = state.snapshot.result
        return QueryResult(original.columns, original.types, tuple(tuple(row) for row in rows), False, original.elapsed_seconds, original.kinds)

    def save_result_table(self, snapshot_id: str, name: str, row_indices: tuple[int, ...] | None = None) -> WorkspaceTable:
        if not isinstance(name, str) or TABLE_NAME_PATTERN.fullmatch(name) is None:
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["table"])
        state, sql, parameters = self._selection(snapshot_id, row_indices)
        connection = self._connection(StorageTarget(StorageMode.MEMORY, None), create=False)
        exists = connection.execute(DuckDBStore.EXISTS_SQL, [DuckDBStore.MAIN_SCHEMA, name]).fetchone()[0]
        if exists:
            raise CdwError(ErrorCode.PRESET_CONFLICT, DuckDBStore.MEMORY_NAME_CONFLICT)
        try:
            connection.execute(f"CREATE TABLE {_qualified(DuckDBStore.MEMORY_CATALOG, DuckDBStore.MAIN_SCHEMA, name)} AS {sql}", parameters)
            updated = _utc_now()
            self._updates[(DuckDBStore.MEMORY_CATALOG, DuckDBStore.MAIN_SCHEMA, name)] = updated
            return WorkspaceTable(_display_qualified(DuckDBStore.MEMORY_CATALOG, name), StorageMode.MEMORY, updated,
                                  tuple(zip(state.snapshot.result.columns, state.snapshot.result.types)), ":memory:",
                                  display_name=name)
        except duckdb.Error as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, str(exc)) from exc

    def copy_result(self, snapshot_id: str, format: str, row_indices: tuple[int, ...] | None = None) -> str:
        try:
            kind = CopyFormat(format)
        except ValueError as exc:
            raise CdwError(ErrorCode.INVALID_PRESET, DuckDBStore.INVALID_EXPORT_FORMAT) from exc
        result = self.result_snapshot(snapshot_id, row_indices)
        return csv_text(result, TEXT_DELIMITER) if kind is CopyFormat.TEXT else html_text(result)

    def export_result(self, snapshot_id: str, path: str | Path, format: str, delimiter: str = DEFAULT_EXPORT_DELIMITER,
                      row_indices: tuple[int, ...] | None = None, protected_paths=()) -> str:
        state, sql, parameters = self._selection(snapshot_id, row_indices)
        try:
            kind = ExportFormat(format)
            if kind is ExportFormat.CSV:
                validate_delimiter(delimiter)
            destination = Path(path).expanduser().resolve()
            for protected in (*protected_paths, *(key for key in self._catalogs if key != ":memory:")):
                candidate = Path(protected).resolve()
                if destination == candidate or (destination.exists() and candidate.exists() and os.path.samefile(destination, candidate)):
                    raise CdwError(ErrorCode.STORAGE_FAILED, DuckDBStore.PROTECTED_EXPORT_PATH)
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=DuckDBStore.EXPORT_TEMP_PREFIX, suffix=destination.suffix, dir=destination.parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                if kind is ExportFormat.DUCKDB:
                    temporary.unlink()
                    catalog = DuckDBStore.EXPORT_CATALOG_PREFIX + uuid4().hex
                    assert self._engine is not None
                    self._engine.execute(f"ATTACH {_quote_literal(str(temporary))} AS {quote_identifier(catalog)}")
                    try:
                        self._engine.execute(f"CREATE TABLE {_qualified(catalog, DuckDBStore.MAIN_SCHEMA, DuckDBStore.EXPORT_TABLE_NAME)} AS {sql}", parameters)
                    finally:
                        self._engine.execute(f"DETACH {quote_identifier(catalog)}")
                else:
                    result = self.result_snapshot(snapshot_id, row_indices)
                    payload = csv_text(result, delimiter) if kind is ExportFormat.CSV else json_text(result)
                    temporary.write_text(payload, encoding=EXPORT_ENCODING, newline="")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
                Path(str(temporary) + ".wal").unlink(missing_ok=True)
            return str(destination)
        except (OSError, ValueError, duckdb.Error) as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, str(exc)) from exc

    def release_result(self, snapshot_id: str) -> None:
        self._check_thread()
        state = self._snapshots.pop(snapshot_id, None)
        if state is not None and self._engine is not None:
            self._engine.execute(f"DROP TABLE IF EXISTS {state.table}")

    def committed(self, target: StorageTarget, run_id: str) -> tuple[int, int] | None:
        try:
            return self._connection(target, create=False).execute(DuckDBStore.FIND_COMMIT_SQL, [run_id]).fetchone()
        except (CdwError, duckdb.Error):
            return None

    def interrupt(self, cancel: threading.Event | None = None) -> None:
        # DuckDB's interrupt API may be invoked from another thread.
        if self._engine is not None:
            self._engine.interrupt()

    def close(self) -> None:
        if self._engine is not None:
            self._check_thread()
            self._engine.close()
            self._engine = None
        self._catalogs = {":memory:": DuckDBStore.MEMORY_CATALOG}
        self._snapshots.clear()
        self._updates.clear()
        self._readonly.clear()
