"""Command output parsing; library-specific CSV/type inference ends here."""

from __future__ import annotations

import csv
import io
import json

import polars as pl

from cdw.domain.contracts import CdwError, ErrorCode, OutputFormat, Preset
from cdw.domain.parsing import ColumnCountMode, DelimiterMode
from cdw.i18n import tr


class OutputParser:
    TEXT_LINE_COLUMN = "line_number"
    TEXT_VALUE_COLUMN = "text"
    CSV_LINE_TERMINATOR = "\n"

    def parse(self, raw: bytes, preset: Preset) -> pl.DataFrame:
        if not raw.strip():
            raise CdwError(ErrorCode.EMPTY_OUTPUT)
        try:
            text = raw.decode(preset.encoding).removeprefix("\ufeff")
            match preset.output_format:
                case OutputFormat.JSON:
                    records = json.loads(text)
                    if isinstance(records, dict):
                        records = [records]
                    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                        raise ValueError(tr("infrastructure.parsers.json_shape_message"))
                    frame = pl.DataFrame(records, infer_schema_length=None)
                case OutputFormat.NDJSON:
                    records = [json.loads(line) for line in text.splitlines() if line.strip()]
                    if any(not isinstance(row, dict) for row in records):
                        raise ValueError(tr("infrastructure.parsers.ndjson_shape_message"))
                    frame = pl.DataFrame(records, infer_schema_length=None)
                case OutputFormat.CSV:
                    frame = self._delimited(text, preset)
                case OutputFormat.TEXT:
                    lines = text.splitlines()
                    frame = pl.DataFrame({self.TEXT_LINE_COLUMN: range(1, len(lines) + 1), self.TEXT_VALUE_COLUMN: lines})
                case _:
                    raise ValueError(tr("infrastructure.parsers.unsupported_format_message"))
        except (ValueError, UnicodeError, pl.exceptions.PolarsError, StopIteration, csv.Error) as exc:
            raise CdwError(ErrorCode.PARSE_FAILED, str(exc)) from exc
        if frame.height == 0 or frame.width == 0:
            raise CdwError(ErrorCode.EMPTY_OUTPUT)
        return frame

    def _delimited(self, text: str, preset: Preset) -> pl.DataFrame:
        policy = preset.parsing
        if policy.delimiter_mode is DelimiterMode.WHITESPACE:
            numbered = [(number, line.split()) for number, line in enumerate(text.splitlines(), 1) if line.strip()]
        else:
            reader = csv.reader(io.StringIO(text), delimiter=preset.csv_delimiter, strict=True)
            numbered = [(reader.line_num, row) for row in reader if row]
        if not numbered:
            raise CdwError(ErrorCode.EMPTY_OUTPUT)
        header = numbered.pop(0)[1] if policy.has_header else None
        if not numbered:
            raise CdwError(ErrorCode.EMPTY_OUTPUT)
        width = (policy.column_count if policy.column_mode is ColumnCountMode.FIXED
                 else len(header) if header is not None else len(numbered[0][1]))
        if header is not None and len(header) != width:
            raise ValueError(tr("collection.parse.header_count").format(expected=width, actual=len(header)))
        for line_number, row in numbered:
            if len(row) != width:
                raise ValueError(tr("collection.parse.ragged_row").format(
                    line=line_number, expected=width, actual=len(row)))
        names = policy.resolve_column_names(width, header)
        # Re-encode already validated fields so Polars owns type inference rather
        # than duplicating CSV and numeric conversion rules in the application.
        normalized = io.StringIO()
        writer = csv.writer(normalized, lineterminator=self.CSV_LINE_TERMINATOR)
        writer.writerows(row for _, row in numbered)
        normalized.seek(0)
        return pl.read_csv(normalized, has_header=False, new_columns=list(names),
                           infer_schema=policy.infer_types, infer_schema_length=None,
                           try_parse_dates=False, truncate_ragged_lines=False)
