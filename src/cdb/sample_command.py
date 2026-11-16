"""A deterministic stdout producer for offline end-to-end demonstrations."""

import json
import sys

SAMPLE_ROWS = (
    {"hostname": "rhel-app-01", "role": "application", "cpu_percent": 23.5, "memory_percent": 48.2, "status": "healthy"},
    {"hostname": "rhel-app-02", "role": "application", "cpu_percent": 41.8, "memory_percent": 62.7, "status": "healthy"},
    {"hostname": "rhel-db-01", "role": "database", "cpu_percent": 67.2, "memory_percent": 79.4, "status": "warning"},
    {"hostname": "rhel-batch-01", "role": "batch", "cpu_percent": 12.1, "memory_percent": 31.0, "status": "healthy"},
    {"hostname": "rhel-log-01", "role": "logging", "cpu_percent": 35.6, "memory_percent": 55.9, "status": "healthy"},
)


def main() -> None:
    sys.stdout.buffer.write(json.dumps(SAMPLE_ROWS, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
