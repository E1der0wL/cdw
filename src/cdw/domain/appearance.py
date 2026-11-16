"""User-editable appearance contract. Internal widget metrics never enter it."""

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from math import isfinite
import re

APPEARANCE_VERSION = 4
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
INVALID_COLOR = "색상은 #RRGGBB 형식으로 입력하거나 비워서 기본값을 사용해 주세요."
FONT_SIZE_RANGE = (8.0, 20.0)
UI_SCALE_RANGE = (0.8, 1.5)
DEFAULT_FONT_SIZE = 10.0
DEFAULT_UI_SCALE = 1.0
SYSTEM_FONT = ""
INVALID_SETTINGS = "화면 설정의 형식 또는 허용 범위를 확인해 주세요."
UNKNOWN_FIELDS = "지원하지 않는 화면 설정 항목입니다."


class ThemeMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class ColorOverrides:
    """Empty values inherit the selected theme's default colors."""

    accent: str = ""
    background: str = ""
    foreground: str = ""
    secondary_background: str = ""

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(INVALID_COLOR)
            value = value.strip().lower()
            if value and HEX_COLOR.fullmatch(value) is None:
                raise ValueError(INVALID_COLOR)
            object.__setattr__(self, name, value)

    @classmethod
    def from_dict(cls, data: dict) -> "ColorOverrides":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError(UNKNOWN_FIELDS)
        return cls(**data)


@dataclass(frozen=True)
class AppearanceSettings:
    mode: ThemeMode = ThemeMode.LIGHT
    font_family: str = SYSTEM_FONT
    code_font_family: str = SYSTEM_FONT
    font_size: float = DEFAULT_FONT_SIZE
    ui_scale: float = DEFAULT_UI_SCALE
    light_colors: ColorOverrides = ColorOverrides()
    dark_colors: ColorOverrides = ColorOverrides()

    def __post_init__(self):
        if not isinstance(self.mode, ThemeMode):
            raise ValueError(INVALID_SETTINGS)
        if not all(isinstance(value, ColorOverrides) for value in (self.light_colors, self.dark_colors)):
            raise ValueError(INVALID_SETTINGS)
        for value in (self.font_family, self.code_font_family):
            if not isinstance(value, str) or any(ord(char) < 32 for char in value):
                raise ValueError(INVALID_SETTINGS)
        for value, bounds in ((self.font_size, FONT_SIZE_RANGE), (self.ui_scale, UI_SCALE_RANGE)):
            if (not isinstance(value, (float, int)) or isinstance(value, bool)
                    or not isfinite(value) or not bounds[0] <= value <= bounds[1]):
                raise ValueError(INVALID_SETTINGS)

    @property
    def active_colors(self) -> ColorOverrides:
        return self.dark_colors if self.mode is ThemeMode.DARK else self.light_colors

    def with_active_colors(self, **values) -> "AppearanceSettings":
        field = "dark_colors" if self.mode is ThemeMode.DARK else "light_colors"
        return replace(self, **{field: replace(self.active_colors, **values)})

    def to_dict(self) -> dict:
        return {"version": APPEARANCE_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "AppearanceSettings":
        if not isinstance(data, dict) or type(data.get("version")) is not int:
            raise ValueError(INVALID_SETTINGS)
        values = dict(data)
        version = values.pop("version")
        if version != APPEARANCE_VERSION:
            raise ValueError(INVALID_SETTINGS)
        if set(values) != set(cls.__dataclass_fields__):
            raise ValueError(UNKNOWN_FIELDS)
        for name in ("light_colors", "dark_colors"):
            values[name] = ColorOverrides.from_dict(values[name])
        values["mode"] = ThemeMode(values["mode"])
        return cls(**values)
