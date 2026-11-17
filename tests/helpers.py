"""Test-only commands and detached-server cleanup; never packaged with CDW."""

import json
import os
import sys
from uuid import uuid4

from cdw.config import DEFAULT_CSV_DELIMITER, DEFAULT_ENCODING
from cdw.domain.contracts import OutputFormat, Preset, StorageMode, StorageTarget, WriteMode


INVENTORY_ROWS = (
    {"hostname": "rhel-app-01", "role": "application", "cpu_percent": 23.5, "memory_percent": 48.2, "status": "healthy"},
    {"hostname": "rhel-app-02", "role": "application", "cpu_percent": 41.8, "memory_percent": 62.7, "status": "healthy"},
    {"hostname": "rhel-db-01", "role": "database", "cpu_percent": 67.2, "memory_percent": 79.4, "status": "warning"},
    {"hostname": "rhel-batch-01", "role": "batch", "cpu_percent": 12.1, "memory_percent": 31.0, "status": "healthy"},
    {"hostname": "rhel-log-01", "role": "logging", "cpu_percent": 35.6, "memory_percent": 55.9, "status": "healthy"},
)


def make_preset(config, *, memory=False):
    payload = json.dumps(INVENTORY_ROWS)
    return Preset(
        id=str(uuid4()), name="Fixture inventory", executable=sys.executable,
        arguments=("-c", f"print({payload!r})"), working_directory=None,
        environment=dict(os.environ), output_format=OutputFormat.JSON,
        encoding=DEFAULT_ENCODING, csv_delimiter=DEFAULT_CSV_DELIMITER,
        storage=StorageTarget(StorageMode.MEMORY, None) if memory else StorageTarget(StorageMode.FILE, str(config.default_database)),
        table_name="host_inventory", write_mode=WriteMode.REPLACE, execution=config.execution_policy,
    )


def stop_server(config):
    from cdw.server.client import ServerClient

    client = ServerClient(config)
    client.stop()
