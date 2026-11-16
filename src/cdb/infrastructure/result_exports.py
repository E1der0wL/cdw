"""One scalar serialization boundary for clipboard, exports and task inputs.

JSON uses ISO temporal strings, exact decimal strings, base64 {$binary: ...}
objects for blobs, and strings for non-finite floats. CSV/HTML use the same
representations; NULL becomes an empty field in those text-oriented formats.
"""

import base64
import csv
import html
import io
import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from cdb.domain.contracts import QueryResult
from cdb.domain.workspace import DEFAULT_EXPORT_DELIMITER, validate_delimiter

EXPORT_ENCODING = "utf-8"
TEXT_DELIMITER = "\t"
CSV_LINE_TERMINATOR = "\n"
JSON_BINARY_KEY = "$binary"
JSON_SEPARATORS = (",", ":")
NON_FINITE_FLOATS = {float("inf"): "Infinity", float("-inf"): "-Infinity"}


def json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else NON_FINITE_FLOATS.get(value, "NaN")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (timedelta, UUID)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {JSON_BINARY_KEY: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return str(value)


def text_value(value) -> str:
    converted = json_value(value)
    if converted is None:
        return ""
    if isinstance(converted, (dict, list)):
        return json.dumps(converted, ensure_ascii=False, allow_nan=False, separators=JSON_SEPARATORS)
    return str(converted)


def csv_text(result: QueryResult, delimiter: str = DEFAULT_EXPORT_DELIMITER) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=validate_delimiter(delimiter), lineterminator=CSV_LINE_TERMINATOR)
    writer.writerow(result.columns)
    writer.writerows(tuple(text_value(value) for value in row) for row in result.rows)
    return output.getvalue()


def json_text(result: QueryResult, envelope: bool = False) -> str:
    rows = [[json_value(value) for value in row] for row in result.rows]
    payload = ({"columns": list(result.columns), "types": list(result.types), "rows": rows} if envelope
               else [dict(zip(result.columns, row)) for row in rows])
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)


def html_text(result: QueryResult) -> str:
    def cell(value):
        return html.escape(text_value(value)).replace("\n", "<br>")
    heading = "".join(f"<th>{html.escape(name)}</th>" for name in result.columns)
    body = "".join("<tr>" + "".join(f"<td>{cell(value)}</td>" for value in row) + "</tr>" for row in result.rows)
    return f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"
