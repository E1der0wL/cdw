from cdw.i18n import tr
"""Heroicons boundary: semantic names, recoloring, caching and DPI-aware SVGs."""

from enum import StrEnum
from functools import lru_cache
from importlib.resources import files
from math import ceil

from qtpy.QtCore import QByteArray, QRectF, Qt
from qtpy.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from qtpy.QtSvg import QSvgRenderer

ASSET_DIRECTORY = "assets/heroicons"
ASSET_CACHE_SIZE = 128


class IconName(StrEnum):
    HOME = "home"
    DATABASE = "circle-stack"
    MEMORY = "cpu-chip"
    SCHEDULE = "calendar-days"
    TASKS = "queue-list"
    SETTINGS = "cog-6-tooth"
    PROFILE = "user-circle"
    TERMINAL = "command-line"
    TABLE = "table-cells"
    PLAY = "play"
    STOP = "stop"
    ADD = "plus"
    EDIT = "pencil-square"
    DELETE = "trash"
    CLOSE = "x-mark"
    MINIMIZE = "minus"
    MAXIMIZE = "arrows-pointing-out"
    RESTORE = "square-2-stack"
    FOLDER = "folder-open"
    DOCUMENT = "document-text"
    REFRESH = "arrow-path"
    RESET = "arrow-uturn-left"
    SUN = "sun"
    MOON = "moon"
    CHECK = "check"
    SUCCESS = "check-circle"
    WARNING = "exclamation-triangle"
    INFO = "information-circle"
    CHEVRON_DOWN = "chevron-down"


class IconVariant(StrEnum):
    OUTLINE = "outline"
    SOLID = "solid"


@lru_cache(maxsize=ASSET_CACHE_SIZE)
def svg_source(name: IconName, variant: IconVariant) -> bytes:
    return files("cdw").joinpath(ASSET_DIRECTORY, variant.value, f"{name.value}.svg").read_bytes()


class SvgIconEngine(QIconEngine):

    INVALID_SVG = tr('icons.invalid_asset')

    def __init__(self, data: bytes, rotation: int = 0) -> None:
        super().__init__()
        self._data = data
        self._rotation = rotation
        self._renderer = QSvgRenderer(QByteArray(data))
        if not self._renderer.isValid():
            raise ValueError(SvgIconEngine.INVALID_SVG.format(name="SVG"))

    def clone(self):
        return SvgIconEngine(self._data, self._rotation)

    def isNull(self):
        return False

    def paint(self, painter, rect, mode, state):
        painter.save()
        center = QRectF(rect).center()
        painter.translate(center)
        painter.rotate(self._rotation)
        painter.translate(-center)
        self._renderer.render(painter, QRectF(rect))
        painter.restore()

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = QPixmap(ceil(size.width() * scale), ceil(size.height() * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRectF(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap


class IconProvider:
    """GUI-thread service. Qt requests fresh vector renders at each display DPI."""

    SOURCE_COLOR = b"currentColor"
    ICON_CACHE_SIZE = 256


    def __init__(self):
        self._cache: dict[tuple, QIcon] = {}

    def icon(self, name: IconName, color: str, variant: IconVariant = IconVariant.OUTLINE, *, rotation: int = 0) -> QIcon:
        name, variant = IconName(name), IconVariant(variant)
        parsed_color = QColor(color)
        if not parsed_color.isValid():
            raise ValueError(tr('icons.invalid_color').format(color=color))
        normalized = parsed_color.name()
        key = name, normalized, variant, rotation % 360
        if key not in self._cache:
            if len(self._cache) >= IconProvider.ICON_CACHE_SIZE:
                self._cache.clear()
            data = svg_source(name, variant).replace(IconProvider.SOURCE_COLOR, normalized.encode("ascii"))
            self._cache[key] = QIcon(SvgIconEngine(data, rotation))
        return QIcon(self._cache[key])

    def clear(self):
        self._cache.clear()
