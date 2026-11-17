"""SQLite connection policy and the complete metadata schema."""

from cdw.i18n import tr
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.serialization import migrate_preset_document_v1, preset_from_dict
from cdw.domain.tasks import TaskDefinition, migrate_task_document_v1
from cdw.infrastructure.schedule_repository import ScheduleRepository


class MetadataDatabase:

    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS presets (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, storage_key TEXT NOT NULL,
        table_name TEXT NOT NULL, definition TEXT NOT NULL,
        UNIQUE(storage_key, table_name)
    );
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, preset_id TEXT NOT NULL, preset_name TEXT NOT NULL,
        state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        row_count INTEGER, elapsed_seconds REAL, error_code TEXT,
        details TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
        storage_key TEXT NOT NULL, table_name TEXT NOT NULL, preset_snapshot TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at DESC);
    CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, definition TEXT NOT NULL);
    """
    CREATE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS workspace_tasks (
        id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, definition TEXT NOT NULL
    )"""

    TIMEOUT_SECONDS = 10
    SCHEMA_VERSION = 3
    VERSION_SQL = "PRAGMA user_version"
    HAS_SCHEMA_SQL = "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%')"
    PRESETS_FOR_MIGRATION_SQL = "SELECT id, definition FROM presets"
    TASKS_FOR_MIGRATION_SQL = "SELECT id, definition FROM workspace_tasks"
    UPDATE_PRESET_DEFINITION_SQL = "UPDATE presets SET definition=? WHERE id=?"
    UPDATE_TASK_DEFINITION_SQL = "UPDATE workspace_tasks SET definition=? WHERE id=?"

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute(self.VERSION_SQL).fetchone()[0]
            if version == self.SCHEMA_VERSION:
                return
            if version not in (0, 2) or (version == 0 and connection.execute(self.HAS_SCHEMA_SQL).fetchone()[0]):
                raise CdwError(ErrorCode.STORAGE_FAILED, tr('infrastructure.metadata_database.metadatadatabase.init'))
            connection.executescript("BEGIN IMMEDIATE;\n" + self.SCHEMA_SQL + self.CREATE_TABLE_SQL + ";\n" + ScheduleRepository.SCHEMA_SQL)
            if version == 2:
                self._migrate_definitions(connection, dict(os.environ))
            connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    def _migrate_definitions(self, connection, environment):
        try:
            for row in connection.execute(self.PRESETS_FOR_MIGRATION_SQL).fetchall():
                document = json.loads(row["definition"])
                if not isinstance(document, dict):
                    raise ValueError(tr("server.migration_failed"))
                if document.get("version") == 1:
                    document = migrate_preset_document_v1(document, environment)
                preset_from_dict(document)
                connection.execute(self.UPDATE_PRESET_DEFINITION_SQL, (json.dumps(document, ensure_ascii=False), row["id"]))
            for row in connection.execute(self.TASKS_FOR_MIGRATION_SQL).fetchall():
                document = json.loads(row["definition"])
                if not isinstance(document, dict):
                    raise ValueError(tr("server.migration_failed"))
                if document.get("version") == 1:
                    document = migrate_task_document_v1(document, environment)
                TaskDefinition.from_dict(document)
                connection.execute(self.UPDATE_TASK_DEFINITION_SQL, (json.dumps(document, ensure_ascii=False), row["id"]))
        except (CdwError, ValueError, TypeError, KeyError) as error:
            raise CdwError(ErrorCode.STORAGE_FAILED, tr("server.migration_failed").format(details=str(error))) from error

    @contextmanager
    def connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=self.TIMEOUT_SECONDS)
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, str(exc)) from exc
        finally:
            if connection is not None:
                connection.close()
