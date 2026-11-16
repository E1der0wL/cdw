from __future__ import annotations

import csv
import io
import json

import polars as pl

from cdb.domain.contracts import CdbError, ErrorCode, OutputFormat, Preset

TEXT_LINE_COLUMN = "line_number"
TEXT_VALUE_COLUMN = "text"
JSON_SHAPE_MESSAGE = "JSON은 객체 하나 또는 객체 배열이어야 합니다."
NDJSON_SHAPE_MESSAGE = "NDJSON의 각 행은 JSON 객체여야 합니다."
CSV_HEADER_MESSAGE = "CSV 헤더는 비어 있지 않은 고유한 컬럼명이어야 합니다."
UNSUPPORTED_FORMAT_MESSAGE = "지원하지 않는 출력 형식입니다."


class OutputParser:
    def parse(self, raw: bytes, preset: Preset) -> pl.DataFrame:
        if not raw.strip():
            raise CdbError(ErrorCode.EMPTY_OUTPUT)
        try:
            text = raw.decode(preset.encoding).removeprefix("\ufeff")
            match preset.output_format:
                case OutputFormat.JSON:
                    records = json.loads(text)
                    if isinstance(records, dict):
                        records = [records]
                    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                        raise ValueError(JSON_SHAPE_MESSAGE)
                    frame = pl.DataFrame(records, infer_schema_length=None)
                case OutputFormat.NDJSON:
                    records = [json.loads(line) for line in text.splitlines() if line.strip()]
                    if any(not isinstance(row, dict) for row in records):
                        raise ValueError(NDJSON_SHAPE_MESSAGE)
                    frame = pl.DataFrame(records, infer_schema_length=None)
                case OutputFormat.CSV:
                    header = next(csv.reader(io.StringIO(text), delimiter=preset.csv_delimiter))
                    if not header or any(not name for name in header) or len(header) != len(set(header)):
                        raise ValueError(CSV_HEADER_MESSAGE)
                    frame = pl.read_csv(io.StringIO(text), separator=preset.csv_delimiter,
                                        infer_schema_length=None, try_parse_dates=False)
                case OutputFormat.TEXT:
                    lines = text.splitlines()
                    frame = pl.DataFrame({TEXT_LINE_COLUMN: range(1, len(lines) + 1), TEXT_VALUE_COLUMN: lines})
                case _:
                    raise ValueError(UNSUPPORTED_FORMAT_MESSAGE)
        except (ValueError, UnicodeError, pl.exceptions.PolarsError, StopIteration, csv.Error) as exc:
            raise CdbError(ErrorCode.PARSE_FAILED, str(exc)) from exc
        if frame.height == 0 or frame.width == 0:
            raise CdbError(ErrorCode.EMPTY_OUTPUT)
        return frame
