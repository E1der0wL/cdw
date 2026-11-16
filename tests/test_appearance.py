"""Validate persisted color choices and resolved theme contracts without Qt."""

import json
import sqlite3

import pytest

from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.appearance import AppearanceSettings, ColorOverrides, ThemeMode
from cdw.domain.contracts import CdwError, ErrorCode
from cdw.ui.design.colors import contrast
from cdw.ui.design.spec import resolve_style


def test_current_appearance_survives_profile_restart(tmp_path):
    settings = AppearanceSettings(mode=ThemeMode.DARK, font_family="Example Font", code_font_family="Example Mono",
                                  font_size=12, ui_scale=1.25).with_active_colors(
        accent="#FA8040", background="#252525", foreground="#eeeeee", secondary_background="#333333")
    service = ApplicationService(build_config(tmp_path))
    try:
        service.save_appearance(settings)
        assert service.load_appearance() == settings
        with sqlite3.connect(service.config.metadata_database) as connection:
            payload = json.loads(connection.execute("SELECT definition FROM app_settings").fetchone()[0])
        assert payload["version"] == 4
        assert "color_theme" not in payload
        assert settings.dark_colors.accent == "#fa8040"
    finally:
        service.close()
    reopened = ApplicationService(build_config(tmp_path))
    try:
        assert reopened.load_appearance() == settings
    finally:
        reopened.close()


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


@pytest.mark.parametrize("version", [1, 2, 3])
def test_removed_appearance_versions_are_rejected_without_rewriting(tmp_path, version):
    unsupported = AppearanceSettings(mode=ThemeMode.DARK).to_dict()
    unsupported.update(version=version, color_theme="violet")
    service = ApplicationService(build_config(tmp_path))
    try:
        with sqlite3.connect(service.config.metadata_database) as connection:
            connection.execute("INSERT INTO app_settings VALUES (?, ?)", ("appearance", json.dumps(unsupported)))
        with pytest.raises(CdwError) as error:
            service.load_appearance()
        assert error.value.code is ErrorCode.STORAGE_FAILED
        with sqlite3.connect(service.config.metadata_database) as connection:
            assert json.loads(connection.execute("SELECT definition FROM app_settings").fetchone()[0]) == unsupported
    finally:
        service.close()


def test_current_settings_reject_removed_accent_preset_field():
    payload = AppearanceSettings().to_dict()
    payload["color_theme"] = "blue"
    with pytest.raises(ValueError):
        AppearanceSettings.from_dict(payload)


@pytest.mark.parametrize("accent", ["#ffff00", "#ffffff", "#000000", "#143b99"])
def test_accent_text_stays_readable_on_normal_and_hover_buttons(accent):
    style = resolve_style(AppearanceSettings(mode=ThemeMode.DARK).with_active_colors(accent=accent))
    assert style.colors["accent"] == accent
    for role in ("accent", "accent_hover"):
        assert contrast(style.colors["on_accent"], style.colors[role]) >= 4.5
    assert contrast(style.colors["accent_text"], style.colors["window"]) >= 4.5
