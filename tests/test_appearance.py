"""Validate persisted color choices and resolved theme contracts without Qt."""

import json
import sqlite3

import pytest

from cdb.application.service import ApplicationService
from cdb.config import build_config
from cdb.domain.appearance import AppearanceSettings, ColorOverrides, ColorTheme, ThemeMode
from cdb.ui.design.colors import contrast
from cdb.ui.design.spec import resolve_style


def test_existing_v1_profile_keeps_preferences_and_adds_optional_colors(tmp_path):
    legacy = {"version": 1, "mode": "dark", "color_theme": "violet", "font_family": "Example Font",
              "code_font_family": "Example Mono", "font_size": 12, "ui_scale": 1.25}
    service = ApplicationService(build_config(tmp_path))
    try:
        with sqlite3.connect(service.config.metadata_database) as connection:
            connection.execute("INSERT INTO app_settings VALUES (?, ?)", ("appearance", json.dumps(legacy)))
        restored = service.load_appearance()
        assert restored == AppearanceSettings(mode=ThemeMode.DARK, color_theme=ColorTheme.VIOLET,
                                              font_family="Example Font", code_font_family="Example Mono", font_size=12, ui_scale=1.25)
        # Reading old profiles is non-destructive; saving explicitly upgrades them.
        with sqlite3.connect(service.config.metadata_database) as connection:
            assert json.loads(connection.execute("SELECT definition FROM app_settings").fetchone()[0]) == legacy
        updated = restored.with_active_colors(accent="#FA8040", background="#252525", foreground="#eeeeee")
        service.save_appearance(updated)
        assert service.load_appearance() == updated
        assert updated.dark_colors.accent == "#fa8040"
        assert updated.light_colors == ColorOverrides()
    finally:
        service.close()


@pytest.mark.parametrize("value", ["#abc", "red", "#12345g", "#00112233", "x; color: red", None, 123])
def test_invalid_color_overrides_are_rejected(value):
    with pytest.raises(ValueError):
        ColorOverrides(background=value)


def test_nested_color_settings_reject_unknown_internal_options():
    payload = AppearanceSettings().to_dict()
    payload["dark_colors"]["radius"] = 12
    with pytest.raises(ValueError):
        AppearanceSettings.from_dict(payload)


@pytest.mark.parametrize("mode,background,secondary", [(ThemeMode.LIGHT, "#f4f6fa", "#e7ebf1"),
                                                       (ThemeMode.DARK, "#202020", "#292d30")])
def test_background_roles_keep_primary_and_secondary_colors_independent(mode, background, secondary):
    style = resolve_style(AppearanceSettings(mode=mode))
    assert style.colors["window"] == style.colors["surface"] == background
    assert style.colors["secondary_surface"] == style.colors["subtle"] == secondary
    if mode is ThemeMode.DARK:
        assert style.colors["sidebar"] == "#141719"
    custom = resolve_style(style.preferences.with_active_colors(background="#34302a", secondary_background="#45413b",
                                                               foreground="#f0e2d0", accent="#fcbf49"))
    assert custom.colors["window"] == custom.colors["surface"] == "#34302a"
    assert custom.colors["secondary_surface"] == custom.colors["subtle"] == "#45413b"
    assert custom.colors["text"] == "#f0e2d0"
    assert custom.colors["accent"] == "#fcbf49"
    assert custom.colors["sidebar"] == style.colors["sidebar"]
    assert custom.colors["log_surface"] == background
    assert custom.colors["border"] != style.colors["border"]
    assert AppearanceSettings.from_dict(custom.preferences.to_dict()) == custom.preferences


def test_existing_v2_colors_survive_secondary_background_upgrade(tmp_path):
    legacy = AppearanceSettings(mode=ThemeMode.DARK, font_size=12).with_active_colors(
        accent="#80cabc", background="#242424", foreground="#ededed").to_dict()
    legacy["version"] = 2
    for mode in ("light_colors", "dark_colors"):
        del legacy[mode]["secondary_background"]
    service = ApplicationService(build_config(tmp_path))
    try:
        with sqlite3.connect(service.config.metadata_database) as connection:
            connection.execute("INSERT INTO app_settings VALUES (?, ?)", ("appearance", json.dumps(legacy)))
        restored = service.load_appearance()
        assert restored.active_colors == ColorOverrides(accent="#80cabc", background="#242424", foreground="#ededed")
        assert restored.font_size == 12
        updated = restored.with_active_colors(secondary_background="#353535")
        service.save_appearance(updated)
        assert service.load_appearance() == updated
        assert updated.to_dict()["version"] == 3
        assert updated.light_colors.secondary_background == ""
    finally:
        service.close()


@pytest.mark.parametrize("accent", ["#ffff00", "#ffffff", "#000000", "#143b99"])
def test_accent_text_stays_readable_on_normal_and_hover_buttons(accent):
    style = resolve_style(AppearanceSettings(mode=ThemeMode.DARK).with_active_colors(accent=accent))
    assert style.colors["accent"] == accent
    for role in ("accent", "accent_hover"):
        assert contrast(style.colors["on_accent"], style.colors[role]) >= 4.5
    assert contrast(style.colors["accent_text"], style.colors["window"]) >= 4.5
