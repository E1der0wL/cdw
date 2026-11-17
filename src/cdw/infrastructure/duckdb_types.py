"""DuckDB type spellings terminate at this adapter."""

from cdw.domain.filtering import ColumnKind

NUMBER_TYPES = frozenset(("TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "UTINYINT",
                          "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "DECIMAL", "NUMERIC",
                          "FLOAT", "DOUBLE", "REAL"))


TEXT_TYPES = frozenset(("VARCHAR", "CHAR", "TEXT", "STRING", "UUID", "ENUM"))


TYPE_KINDS = {
    **dict.fromkeys(NUMBER_TYPES, ColumnKind.NUMBER),
    **dict.fromkeys(TEXT_TYPES, ColumnKind.TEXT),
    "DATE": ColumnKind.DATE,
    "TIMESTAMP": ColumnKind.DATETIME, "TIMESTAMPTZ": ColumnKind.DATETIME,
    "TIMESTAMP_S": ColumnKind.DATETIME, "TIMESTAMP_MS": ColumnKind.DATETIME,
    "TIMESTAMP_NS": ColumnKind.DATETIME, "TIMESTAMP WITH TIME ZONE": ColumnKind.DATETIME,
    "TIMESTAMP WITHOUT TIME ZONE": ColumnKind.DATETIME, "DATETIME": ColumnKind.DATETIME,
    "TIME": ColumnKind.TIME, "TIMETZ": ColumnKind.TIME,
    "TIME WITH TIME ZONE": ColumnKind.TIME, "TIME WITHOUT TIME ZONE": ColumnKind.TIME,
    "BOOLEAN": ColumnKind.BOOLEAN, "BOOL": ColumnKind.BOOLEAN,
}


def column_kind(type_name: str) -> ColumnKind:
    name = type_name.upper().strip()
    # DECIMAL(10, 2)[] and TIMESTAMP[] are collections, not scalar columns.
    if name.endswith("]"):
        return ColumnKind.OTHER
    token = name.split("(", 1)[0].strip()
    return TYPE_KINDS.get(token, ColumnKind.OTHER)
