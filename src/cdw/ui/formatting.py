"""Shared presentation formatting, independent of a particular page."""
from datetime import datetime

TIME_FORMAT = "%m-%d %H:%M:%S"
EMPTY_VALUE = "—"


def format_time(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone().strftime(TIME_FORMAT) if value else EMPTY_VALUE
