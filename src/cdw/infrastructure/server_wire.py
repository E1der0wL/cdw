"""Versioned JSON framing for local IPC; never deserialize executable objects."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
import importlib
import json
import math
from pathlib import Path
import socket
import struct
from uuid import UUID
from cdw.i18n import tr


class WireCodec:
    VERSION = 2
    MAX_MESSAGE_BYTES = 128 * 1024 * 1024
    HEADER = struct.Struct("!I")
    ENCODING = "utf-8"
    UNSUPPORTED_VALUE = tr("server.wire.unsupported_value")
    RESULT_LIMIT = tr("server.wire.result_limit")
    INVALID_LENGTH = tr("server.wire.invalid_length")
    CONNECTION_CLOSED = tr("server.wire.connection_closed")
    CONTRACT_MODULES = (
        "cdw.domain.contracts", "cdw.domain.appearance", "cdw.domain.workspace",
        "cdw.domain.filtering", "cdw.domain.tasks", "cdw.domain.execution",
        "cdw.domain.schedules", "cdw.domain.parsing",
    )

    def __init__(self):
        self._types = {}
        for name in self.CONTRACT_MODULES:
            try:
                module = importlib.import_module(name)
            except ModuleNotFoundError as error:
                if error.name == name:
                    continue
                raise
            for value in vars(module).values():
                if isinstance(value, type) and value.__module__ == name and (is_dataclass(value) or issubclass(value, Enum)):
                    self._types[f"{name}.{value.__name__}"] = value

    def encode(self, value):
        if isinstance(value, Enum):
            return {"$enum": f"{type(value).__module__}.{type(value).__name__}", "value": value.value}
        if is_dataclass(value) and not isinstance(value, type):
            return {"$type": f"{type(value).__module__}.{type(value).__name__}",
                    "fields": {field.name: self.encode(getattr(value, field.name)) for field in fields(value)}}
        if isinstance(value, Mapping):
            return {"$map": [[self.encode(key), self.encode(item)] for key, item in value.items()]}
        if isinstance(value, tuple):
            return {"$tuple": [self.encode(item) for item in value]}
        if isinstance(value, (set, frozenset)):
            return {"$set": [self.encode(item) for item in value]}
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if isinstance(value, bytes):
            return {"$bytes": base64.b64encode(value).decode("ascii")}
        if isinstance(value, (datetime, date, time)):
            return {"$temporal": type(value).__name__, "value": value.isoformat()}
        if isinstance(value, timedelta):
            return {"$duration": [value.days, value.seconds, value.microseconds]}
        if isinstance(value, Decimal):
            return {"$decimal": str(value)}
        if isinstance(value, UUID):
            return {"$uuid": str(value)}
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, float) and not math.isfinite(value):
            return {"$float": repr(value)}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(self.UNSUPPORTED_VALUE.format(type=type(value).__name__))

    def decode(self, value):
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$map" in value:
            return {self.decode(key): self.decode(item) for key, item in value["$map"]}
        if "$type" in value:
            cls = self._types[value["$type"]]
            return cls(**{key: self.decode(item) for key, item in value["fields"].items()})
        if "$enum" in value:
            return self._types[value["$enum"]](value["value"])
        if "$tuple" in value:
            return tuple(self.decode(item) for item in value["$tuple"])
        if "$set" in value:
            return frozenset(self.decode(item) for item in value["$set"])
        if "$bytes" in value:
            return base64.b64decode(value["$bytes"], validate=True)
        if "$temporal" in value:
            return {"datetime": datetime, "date": date, "time": time}[value["$temporal"]].fromisoformat(value["value"])
        if "$duration" in value:
            return timedelta(days=value["$duration"][0], seconds=value["$duration"][1], microseconds=value["$duration"][2])
        if "$decimal" in value:
            return Decimal(value["$decimal"])
        if "$uuid" in value:
            return UUID(value["$uuid"])
        if "$float" in value:
            return float(value["$float"])
        return {key: self.decode(item) for key, item in value.items()}

    def send(self, connection: socket.socket, value):
        payload = json.dumps(self.encode(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(self.ENCODING)
        if len(payload) > self.MAX_MESSAGE_BYTES:
            raise ValueError(self.RESULT_LIMIT)
        connection.sendall(self.HEADER.pack(len(payload)) + payload)

    def receive(self, connection: socket.socket, *, max_bytes=None):
        length, = self.HEADER.unpack(self._read(connection, self.HEADER.size))
        limit = self.MAX_MESSAGE_BYTES if max_bytes is None else min(max_bytes, self.MAX_MESSAGE_BYTES)
        if not 0 < length <= limit:
            raise ValueError(self.INVALID_LENGTH)
        return self.decode(json.loads(self._read(connection, length).decode(self.ENCODING)))

    @staticmethod
    def _read(connection, length):
        chunks = bytearray()
        while len(chunks) < length:
            chunk = connection.recv(length - len(chunks))
            if not chunk:
                raise ConnectionError(WireCodec.CONNECTION_CLOSED)
            chunks.extend(chunk)
        return bytes(chunks)
