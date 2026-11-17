"""User-editable appearance contract. Internal widget metrics never enter it."""

from cdw.i18n import tr

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from math import isfinite
import re

FONT_SIZE_RANGE = (8.0, 20.0)
UI_SCALE_RANGE = (0.8, 1.5)
UNKNOWN_FIELDS = tr('domain.appearance.unknown_fields')


class ThemeMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class ColorOverrides:
    """Empty values inherit the selected theme's default colors."""

    HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
    INVALID_COLOR = tr('domain.appearance.invalid_color')


    accent: str = ""
    background: str = ""
    foreground: str = ""
    secondary_background: str = ""

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(ColorOverrides.INVALID_COLOR)
            value = value.strip().lower()
            if value and ColorOverrides.HEX_COLOR.fullmatch(value) is None:
                raise ValueError(ColorOverrides.INVALID_COLOR)
            object.__setattr__(self, name, value)

    @classmethod
    def from_dict(cls, data: dict) -> "ColorOverrides":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError(UNKNOWN_FIELDS)
        return cls(**data)


@dataclass(frozen=True)
class AppearanceSettings:

    APPEARANCE_VERSION = 4
    DEFAULT_FONT_SIZE = 10.0
    DEFAULT_UI_SCALE = 1.0
    SYSTEM_FONT = ""
    INVALID_SETTINGS = tr('domain.appearance.invalid_settings')

    mode: ThemeMode = ThemeMode.LIGHT
    font_family: str = SYSTEM_FONT
    code_font_family: str = SYSTEM_FONT
    font_size: float = DEFAULT_FONT_SIZE
    ui_scale: float = DEFAULT_UI_SCALE
    light_colors: ColorOverrides = ColorOverrides()
    dark_colors: ColorOverrides = ColorOverrides()

    def __post_init__(self):
        if not isinstance(self.mode, ThemeMode):
            raise ValueError(AppearanceSettings.INVALID_SETTINGS)
        if not all(isinstance(value, ColorOverrides) for value in (self.light_colors, self.dark_colors)):
            raise ValueError(AppearanceSettings.INVALID_SETTINGS)
        for value in (self.font_family, self.code_font_family):
            if not isinstance(value, str) or any(ord(char) < 32 for char in value):
                raise ValueError(AppearanceSettings.INVALID_SETTINGS)
        for value, bounds in ((self.font_size, FONT_SIZE_RANGE), (self.ui_scale, UI_SCALE_RANGE)):
            if (not isinstance(value, (float, int)) or isinstance(value, bool)
                    or not isfinite(value) or not bounds[0] <= value <= bounds[1]):
                raise ValueError(AppearanceSettings.INVALID_SETTINGS)

    @property
    def active_colors(self) -> ColorOverrides:
        return self.dark_colors if self.mode is ThemeMode.DARK else self.light_colors

    def with_active_colors(self, **values) -> "AppearanceSettings":
        field = "dark_colors" if self.mode is ThemeMode.DARK else "light_colors"
        return replace(self, **{field: replace(self.active_colors, **values)})

    def to_dict(self) -> dict:
        return {"version": AppearanceSettings.APPEARANCE_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "AppearanceSettings":
        if not isinstance(data, dict) or type(data.get("version")) is not int:
            raise ValueError(AppearanceSettings.INVALID_SETTINGS)
        values = dict(data)
        version = values.pop("version")
        if version != AppearanceSettings.APPEARANCE_VERSION:
            raise ValueError(AppearanceSettings.INVALID_SETTINGS)
        if set(values) != set(cls.__dataclass_fields__):
            raise ValueError(UNKNOWN_FIELDS)
        for name in ("light_colors", "dark_colors"):
            values[name] = ColorOverrides.from_dict(values[name])
        values["mode"] = ThemeMode(values["mode"])
        return cls(**values)
