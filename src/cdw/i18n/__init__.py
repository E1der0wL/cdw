"""UTF-8 language packs, selected once at process startup.

Set CDW_LANGUAGE_PACK to a JSON file to override the bundled Korean catalog.
Partial packs fall back per key. Protocol identifiers and user data are never
translated. Restart the application after changing the selected language pack.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from string import Formatter
from types import MappingProxyType


class Catalog:
    ENVIRONMENT_VARIABLE = "CDW_LANGUAGE_PACK"
    DEFAULT_RESOURCE = "locales/ko.json"
    ENCODING = "utf-8"
    # Bootstrap diagnostics cannot depend on the language pack being valid.
    INVALID_MAPPING = "A language pack must be a JSON object of string keys and values"
    UNKNOWN_KEY = "Unknown language-pack key: {key}"
    INVALID_PLACEHOLDERS = "Language-pack placeholders differ: {key}"

    def __init__(self, defaults: Mapping[str, str], overrides: Mapping[str, str] | None = None):
        self._validate_mapping(defaults)
        values = dict(defaults)
        if overrides is not None:
            self._validate_mapping(overrides)
            for key, value in overrides.items():
                if key not in defaults:
                    raise ValueError(self.UNKNOWN_KEY.format(key=key))
                if self._fields(value) != self._fields(defaults[key]):
                    raise ValueError(self.INVALID_PLACEHOLDERS.format(key=key))
                values[key] = value
        self._messages = MappingProxyType(values)

    @staticmethod
    def _validate_mapping(values):
        if not isinstance(values, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
            raise ValueError(Catalog.INVALID_MAPPING)

    @classmethod
    def _fields(cls, template):
        fields = set()
        for _, field, spec, _ in Formatter().parse(template):
            if field is not None:
                fields.add(field)
                fields.update(cls._fields(spec))
        return fields

    @classmethod
    def load(cls, path: Path | None = None):
        defaults = json.loads(files(__package__).joinpath(cls.DEFAULT_RESOURCE).read_text(encoding=cls.ENCODING))
        overrides = json.loads(path.read_text(encoding=cls.ENCODING)) if path is not None else None
        return cls(defaults, overrides)

    def text(self, key: str) -> str:
        return self._messages[key]


_pack_path = os.environ.get(Catalog.ENVIRONMENT_VARIABLE)
_catalog = Catalog.load(Path(_pack_path) if _pack_path else None)


def tr(key: str) -> str:
    """Return the translated template; callers supply named format values."""
    return _catalog.text(key)
