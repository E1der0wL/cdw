"""CDW table-file protocol v1; native DB type spellings end at this adapter.

The envelope has protocol/version/run_id/table_name/collected_at/columns/types/rows.
Columns and stable types are ordered arrays parallel to each row. Decimal cells
are exact strings, dates/times use ISO 8601, binary cells use {$binary: base64},
and NULL is JSON null. Non-finite floats use NaN/Infinity/-Infinity strings.
Integer cells are JSON integer literals and receivers must parse them exactly.
CSV input uses the existing CSV export scalar rules and always has a header.
"""

from __future__ import annotations

import json

from cdw.domain.contracts import QueryResult
from cdw.domain.filtering import ColumnKind
from cdw.domain.tasks import TaskInputContext
from cdw.infrastructure.result_exports import json_value


class TaskTableProtocol:
    NAME = "cdw.table"
    VERSION = 1
    ENCODING = "utf-8"
    JSON_INDENT = 2
    INTEGER_TYPES = frozenset(("TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "BIGNUM",
                               "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"))
    DECIMAL_TYPES = frozenset(("DECIMAL", "NUMERIC"))
    FLOAT_TYPES = frozenset(("FLOAT", "DOUBLE", "REAL"))
    BINARY_TYPES = frozenset(("BLOB", "BYTEA", "BINARY", "VARBINARY"))
    NESTED_PREFIXES = ("STRUCT", "MAP", "UNION")
    KIND_TYPES = {
        ColumnKind.NUMBER: "number", ColumnKind.DATE: "date", ColumnKind.DATETIME: "datetime",
        ColumnKind.TIME: "time", ColumnKind.BOOLEAN: "boolean", ColumnKind.TEXT: "string",
        ColumnKind.OTHER: "string",
    }

    @classmethod
    def column_type(cls, native_type: str, kind: ColumnKind) -> str:
        native = native_type.strip().upper()
        if native.endswith("]") or native.startswith(cls.NESTED_PREFIXES) or native == "JSON":
            return "json"
        token = native.split("(", 1)[0].strip()
        if token in cls.INTEGER_TYPES:
            return "integer"
        if token in cls.DECIMAL_TYPES:
            return "decimal"
        if token in cls.FLOAT_TYPES:
            return "float"
        if token in cls.BINARY_TYPES:
            return "binary"
        if token == "INTERVAL":
            return "duration"
        return cls.KIND_TYPES[kind]

    @classmethod
    def document(cls, result: QueryResult, context: TaskInputContext) -> dict:
        return {
            "protocol": cls.NAME,
            "version": cls.VERSION,
            "run_id": context.run_id,
            "table_name": context.table_name,
            "collected_at": context.collected_at,
            "columns": list(result.columns),
            "types": [cls.column_type(native, kind) for native, kind in zip(result.types, result.kinds)],
            "rows": [[json_value(value) for value in row] for row in result.rows],
        }

    @classmethod
    def serialize(cls, result: QueryResult, context: TaskInputContext) -> str:
        return json.dumps(cls.document(result, context), ensure_ascii=False,
                          allow_nan=False, indent=cls.JSON_INDENT)
