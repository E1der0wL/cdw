"""The authoritative, immutable internal design specification (no Qt imports)."""

from cdw.i18n import tr

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from cdw.domain.appearance import AppearanceSettings, ThemeMode
from cdw.ui.design.colors import blend, contrasting_text, legible_text


class MetricMode(StrEnum):
    FIXED = "fixed"
    SCALED = "scaled"
    CLAMPED = "clamped"


class ButtonVariant(StrEnum):
    SECONDARY = "secondary"
    PRIMARY = "primary"
    DANGER = "danger"
    NAVIGATION = "navigation"
    FIELD_ACTION = "field_action"


class VisualState(StrEnum):
    NORMAL = "normal"
    HOVER = "hover"
    SELECTED = "selected"
    SELECTED_HOVER = "selected_hover"
    DISABLED = "disabled"


class TextRole(StrEnum):
    BODY = "body"
    MUTED = "muted"
    HEADING = "heading"
    SECTION_HEADING = "section_heading"
    METRIC = "metric"
    BRAND = "brand"
    CAPTION = "caption"
    EYEBROW = "eyebrow"
    ERROR = "error"
    CODE = "code"
    SQL_CODE = "sql_code"


CODE_TEXT_ROLES = frozenset((TextRole.CODE, TextRole.SQL_CODE))


class LayoutRole(StrEnum):
    FLUSH = "flush"
    ROW = "row"
    CONTENT = "content"
    PAGE = "page"
    TITLE = "title"
    SECTION = "section"
    SECTIONS = "sections"
    CARD = "card"
    FORM = "form"
    SIDEBAR = "sidebar"
    DIALOG = "dialog"
    WINDOW_TITLEBAR = "window_titlebar"


@dataclass(frozen=True)
class MetricRule:
    base: float
    mode: MetricMode = MetricMode.FIXED
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self):
        if self.base < 0:
            raise ValueError(tr('ui.design.spec.metricrule.post_init'))
        if self.mode is MetricMode.CLAMPED and (
            self.minimum is None or self.maximum is None or not 0 <= self.minimum <= self.base <= self.maximum
        ):
            raise ValueError(tr('ui.design.spec.metricrule.post_init_2'))

    def resolve(self, scale: float) -> int:
        value = self.base if self.mode is MetricMode.FIXED else self.base * scale
        if self.mode is MetricMode.CLAMPED:
            value = max(self.minimum, min(self.maximum, value))
        return round(value)


class MotionRole(StrEnum):
    PAGE = "page"
    TAB = "tab"
    SIDEBAR = "sidebar"


class MotionEasing(StrEnum):
    OUT_CUBIC = "out_cubic"


@dataclass(frozen=True)
class ResolvedMotion:
    duration_ms: int
    distance: int
    easing: MotionEasing


@dataclass(frozen=True)
class MotionRule:
    duration_ms: int
    distance: MetricRule
    easing: MotionEasing = MotionEasing.OUT_CUBIC

    def resolve(self, scale: float) -> ResolvedMotion:
        return ResolvedMotion(self.duration_ms, self.distance.resolve(scale), self.easing)


@dataclass(frozen=True)
class ColorRecipe:
    background: str
    foreground: str
    border: str


@dataclass(frozen=True)
class FontRule:
    multiplier: float
    bold: bool
    color: str


@dataclass(frozen=True)
class LayoutRecipe:
    margins: tuple[str, str, str, str]
    spacing: str


# Fixed geometry, proportional geometry and clamped geometry are selected here.
METRICS = MappingProxyType({
    "zero": MetricRule(0),
    "border": MetricRule(1),
    "radius": MetricRule(8, MetricMode.CLAMPED, 6, 12),
    # A lone bottom border stays straight, including integrated field actions.
    "underline_radius": MetricRule(0),
    # Floating windows stay opaque/rectangular on the non-composited X11 target.
    "popup_radius": MetricRule(0),
    "log_padding_y": MetricRule(2),
    "padding_x": MetricRule(12, MetricMode.CLAMPED, 10, 18),
    "padding_y": MetricRule(7, MetricMode.CLAMPED, 5, 10),
    "gap": MetricRule(12, MetricMode.CLAMPED, 8, 18),
    "gap_small": MetricRule(8, MetricMode.CLAMPED, 6, 12),
    "page_margin": MetricRule(24, MetricMode.CLAMPED, 16, 32),
    "section_padding": MetricRule(20, MetricMode.CLAMPED, 16, 28),
    "section_gap": MetricRule(16, MetricMode.CLAMPED, 12, 24),
    "card_margin": MetricRule(18, MetricMode.CLAMPED, 14, 24),
    "sidebar_width": MetricRule(210, MetricMode.SCALED),
    "sidebar_min_width": MetricRule(170, MetricMode.SCALED),
    "sidebar_max_width": MetricRule(420, MetricMode.SCALED),
    "sidebar_resize_handle": MetricRule(8),
    "icon": MetricRule(18, MetricMode.SCALED),
    "nav_icon": MetricRule(20, MetricMode.SCALED),
    "spin_icon": MetricRule(12, MetricMode.SCALED),
    "control_height": MetricRule(34, MetricMode.SCALED),
    "field_button_width": MetricRule(28, MetricMode.SCALED),
    "filter_popup_width": MetricRule(380, MetricMode.SCALED),
    "filter_popup_height": MetricRule(560, MetricMode.SCALED),
    "filter_values_height": MetricRule(200, MetricMode.SCALED),
    "filter_header_button_width": MetricRule(28, MetricMode.SCALED),
    "result_dialog_height": MetricRule(300, MetricMode.SCALED),
    "catalog_type_width": MetricRule(70, MetricMode.SCALED),
    "catalog_time_width": MetricRule(120, MetricMode.SCALED),
    "table_row": MetricRule(34, MetricMode.SCALED),
    "editor_height": MetricRule(140, MetricMode.SCALED),
    "compact_editor_height": MetricRule(100, MetricMode.SCALED),
    "splitter_handle": MetricRule(4),
    "window_width": MetricRule(1320),
    "window_height": MetricRule(900),
    "window_titlebar_height": MetricRule(44, MetricMode.SCALED),
    "window_resize_margin": MetricRule(6),
    "window_titlebar_padding": MetricRule(8, MetricMode.CLAMPED, 6, 12),
    "window_control_width": MetricRule(38, MetricMode.SCALED),
    "minimum_width": MetricRule(960),
    "minimum_height": MetricRule(640),
    "dialog_width": MetricRule(700, MetricMode.SCALED),
    "dialog_height": MetricRule(660, MetricMode.SCALED),
    "preview_width": MetricRule(880, MetricMode.SCALED),
    "preview_height": MetricRule(700, MetricMode.SCALED),
})

LAYOUTS = MappingProxyType({
    LayoutRole.FLUSH: LayoutRecipe(("zero",) * 4, "zero"),
    LayoutRole.ROW: LayoutRecipe(("zero",) * 4, "gap_small"),
    LayoutRole.CONTENT: LayoutRecipe(("zero",) * 4, "gap"),
    LayoutRole.PAGE: LayoutRecipe(("page_margin",) * 4, "section_gap"),
    LayoutRole.TITLE: LayoutRecipe(("section_padding",) * 4, "gap_small"),
    LayoutRole.SECTION: LayoutRecipe(("section_padding",) * 4, "gap"),
    LayoutRole.SECTIONS: LayoutRecipe(("zero",) * 4, "section_gap"),
    LayoutRole.CARD: LayoutRecipe(("card_margin",) * 4, "gap"),
    LayoutRole.FORM: LayoutRecipe(("zero",) * 4, "gap"),
    LayoutRole.SIDEBAR: LayoutRecipe(("section_padding",) * 4, "gap_small"),
    LayoutRole.DIALOG: LayoutRecipe(("card_margin",) * 4, "gap"),
    LayoutRole.WINDOW_TITLEBAR: LayoutRecipe(("window_titlebar_padding", "zero", "window_titlebar_padding", "zero"), "window_titlebar_padding"),
})

MOTIONS = MappingProxyType({
    MotionRole.PAGE: MotionRule(180, MetricRule(12, MetricMode.CLAMPED, 8, 16)),
    MotionRole.TAB: MotionRule(150, MetricRule(8, MetricMode.CLAMPED, 6, 12)),
    MotionRole.SIDEBAR: MotionRule(200, MetricRule(0)),
})

FONT_RULES = MappingProxyType({
    TextRole.BODY: FontRule(1.0, False, "text"),
    TextRole.MUTED: FontRule(1.0, False, "muted"),
    TextRole.HEADING: FontRule(1.8, True, "text"),
    TextRole.SECTION_HEADING: FontRule(1.05, True, "text"),
    TextRole.METRIC: FontRule(2.0, True, "text"),
    TextRole.BRAND: FontRule(2.0, True, "text"),
    TextRole.CAPTION: FontRule(0.85, False, "muted"),
    TextRole.EYEBROW: FontRule(0.85, True, "accent_text"),
    TextRole.ERROR: FontRule(1.0, False, "error"),
    TextRole.CODE: FontRule(1.0, False, "text"),
    TextRole.SQL_CODE: FontRule(1.2, False, "text"),
})

LIGHT_COLORS = MappingProxyType({
    "sidebar": "#e7edf5", "window": "#f4f6fa", "text": "#202d40",
    "secondary_surface": "#e7ebf1",
    "error": "#b42332", "error_hover": "#921b28", "on_error": "#ffffff",
    "success": "#137157", "warning": "#8a5700",
})
DARK_COLORS = MappingProxyType({
    "sidebar": "#141719", "window": "#202020", "text": "#e8edf5",
    "secondary_surface": "#292d30",
    "error": "#ff8592", "error_hover": "#ffabb5", "on_error": "#291017",
    "success": "#7dd9b6", "warning": "#f4cb7c",
})
# Interaction colors derive from the active mode and user overrides.
DEFAULT_ACCENTS = MappingProxyType({
    ThemeMode.LIGHT: "#137c6c",
    ThemeMode.DARK: "#6bd3bc",
})
COLOR_BLEND_WEIGHTS = MappingProxyType({
    "muted": 0.7, "border": 0.23, "hover": 0.08, "disabled_text": 0.4,
    "sidebar_hover": 0.08, "selection": 0.22, "accent_hover": 0.12,
})
BACKGROUND_ROLES = ("window", "surface", "disabled_bg", "tooltip_bg")


def _recipes(normal, hover, selected, disabled):
    return MappingProxyType({VisualState.NORMAL: normal, VisualState.HOVER: hover,
                             VisualState.SELECTED: selected, VisualState.SELECTED_HOVER: selected,
                             VisualState.DISABLED: disabled})


DISABLED_RECIPE = ColorRecipe("disabled_bg", "disabled_text", "border")
FIELD_ICON_COLORS = MappingProxyType({
    VisualState.NORMAL: "muted", VisualState.HOVER: "text",
    VisualState.SELECTED: "accent_text", VisualState.SELECTED_HOVER: "accent_text",
    VisualState.DISABLED: "disabled_text",
})
BUTTON_RECIPES = MappingProxyType({
    ButtonVariant.SECONDARY: _recipes(ColorRecipe("surface", "text", "border"), ColorRecipe("hover", "text", "accent"),
                                     ColorRecipe("selection", "selection_text", "accent"), DISABLED_RECIPE),
    ButtonVariant.PRIMARY: _recipes(ColorRecipe("accent", "on_accent", "accent"), ColorRecipe("accent_hover", "on_accent", "accent_hover"),
                                   ColorRecipe("accent_hover", "on_accent", "accent_hover"), DISABLED_RECIPE),
    ButtonVariant.DANGER: _recipes(ColorRecipe("error", "on_error", "error"), ColorRecipe("error_hover", "on_error", "error_hover"),
                                  ColorRecipe("error_hover", "on_error", "error_hover"), DISABLED_RECIPE),
    ButtonVariant.NAVIGATION: _recipes(ColorRecipe("sidebar", "muted", "sidebar"), ColorRecipe("sidebar_hover", "text", "sidebar_hover"),
                                      ColorRecipe("selection", "selection_text", "selection"), DISABLED_RECIPE),
    ButtonVariant.FIELD_ACTION: _recipes(ColorRecipe("surface", "muted", "surface"), ColorRecipe("hover", "text", "hover"),
                                        ColorRecipe("selection", "selection_text", "selection"), DISABLED_RECIPE),
})


@dataclass(frozen=True)
class StyleSpec:
    metrics: Mapping[str, MetricRule]
    layouts: Mapping[LayoutRole, LayoutRecipe]
    fonts: Mapping[TextRole, FontRule]
    buttons: Mapping[ButtonVariant, Mapping[VisualState, ColorRecipe]]
    field_icon_colors: Mapping[VisualState, str]
    motions: Mapping[MotionRole, MotionRule]


STYLE_SPEC = StyleSpec(METRICS, LAYOUTS, FONT_RULES, BUTTON_RECIPES, FIELD_ICON_COLORS, MOTIONS)


@dataclass(frozen=True)
class ResolvedStyle:
    preferences: AppearanceSettings
    colors: Mapping[str, str]
    metrics: Mapping[str, int]
    spec: StyleSpec

    def metric(self, name: str) -> int:
        return self.metrics[name]

    def motion(self, role: MotionRole) -> ResolvedMotion:
        return self.spec.motions[MotionRole(role)].resolve(self.preferences.ui_scale)

    def font_points(self, role: TextRole = TextRole.BODY) -> float:
        return self.preferences.font_size * self.preferences.ui_scale * self.spec.fonts[role].multiplier

    def button_colors(self, variant: ButtonVariant, state: VisualState) -> tuple[str, str, str]:
        recipe = self.spec.buttons[variant][state]
        return tuple(self.colors[key] for key in (recipe.background, recipe.foreground, recipe.border))

    def field_icon_color(self, state: VisualState) -> str:
        return self.colors[self.spec.field_icon_colors[state]]

    def layout(self, role: LayoutRole) -> tuple[tuple[int, int, int, int], int]:
        recipe = self.spec.layouts[role]
        return tuple(self.metric(name) for name in recipe.margins), self.metric(recipe.spacing)


def resolve_style(preferences: AppearanceSettings, spec: StyleSpec = STYLE_SPEC) -> ResolvedStyle:
    colors = dict(LIGHT_COLORS if preferences.mode is ThemeMode.LIGHT else DARK_COLORS)
    colors["log_surface"] = colors["window"]
    custom = preferences.active_colors
    background = custom.background or colors["window"]
    foreground = custom.foreground or colors["text"]
    accent = custom.accent or DEFAULT_ACCENTS[preferences.mode]
    colors.update({role: background for role in BACKGROUND_ROLES})
    secondary = custom.secondary_background or colors["secondary_surface"]
    colors.update(secondary_surface=secondary, subtle=secondary)
    colors.update(text=foreground, tooltip_text=foreground, accent=accent)
    for role in ("muted", "border", "hover", "disabled_text"):
        colors[role] = blend(background, foreground, COLOR_BLEND_WEIGHTS[role])
    colors["sidebar_hover"] = blend(colors["sidebar"], foreground, COLOR_BLEND_WEIGHTS["sidebar_hover"])
    colors["selection"] = blend(background, accent, COLOR_BLEND_WEIGHTS["selection"])
    colors["selection_text"] = legible_text(foreground, colors["selection"])
    colors["accent_text"] = legible_text(accent, background)
    colors["on_accent"] = contrasting_text(accent)
    colors["accent_hover"] = blend(accent, contrasting_text(colors["on_accent"]), COLOR_BLEND_WEIGHTS["accent_hover"])
    metrics = {name: rule.resolve(preferences.ui_scale) for name, rule in spec.metrics.items()}
    return ResolvedStyle(preferences, MappingProxyType(colors), MappingProxyType(metrics), spec)
