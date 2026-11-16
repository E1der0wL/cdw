"""One demo preset shared by the UI and CLI."""

import sys
from uuid import uuid4

from cdb.config import AppConfig, DEFAULT_CSV_DELIMITER, DEFAULT_ENCODING
from cdb.domain.contracts import OutputFormat, Preset, StorageMode, StorageTarget, WriteMode

DEMO_NAME = "서버 상태 · 데모"
DEMO_TABLE = "host_inventory"
DEMO_ARGUMENTS = ("-m", "cdb.sample_command")


def demo_preset(config: AppConfig, *, memory: bool = False) -> Preset:
    return Preset(
        id=str(uuid4()), name=DEMO_NAME, executable=sys.executable, arguments=DEMO_ARGUMENTS,
        working_directory=None, environment={}, output_format=OutputFormat.JSON,
        encoding=DEFAULT_ENCODING, csv_delimiter=DEFAULT_CSV_DELIMITER,
        storage=StorageTarget(StorageMode.MEMORY, None) if memory else StorageTarget(StorageMode.FILE, str(config.default_database)),
        table_name=DEMO_TABLE, write_mode=WriteMode.REPLACE, execution=config.execution_policy,
    )
