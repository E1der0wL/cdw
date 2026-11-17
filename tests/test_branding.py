"""CDW launch and data-location contracts without compatibility aliases."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from cdw import __version__
from cdw.branding import APP_NAME
from cdw.config import build_config


def test_current_data_directory_and_explicit_override(monkeypatch, tmp_path):
    assert APP_NAME == "CDW"
    monkeypatch.setenv("CDB_DATA_DIR", str(tmp_path / "old-profile"))
    monkeypatch.delenv("CDW_DATA_DIR", raising=False)
    base = tmp_path / "data"
    monkeypatch.setenv("LOCALAPPDATA" if os.name == "nt" else "XDG_DATA_HOME", str(base))
    assert build_config().data_directory == base / "cdw"
    current = tmp_path / "current-profile"
    explicit = tmp_path / "explicit-profile"
    monkeypatch.setenv("CDW_DATA_DIR", str(current))
    assert build_config().data_directory == current
    assert build_config(explicit).data_directory == explicit


def test_only_current_package_and_launch_commands_are_available():
    assert importlib.util.find_spec("cdb") is None
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {
        "cdw": "cdw.cli:main", "cdw-gui": "cdw.__main__:main", "cdw-server": "cdw.server.__main__:main"}
    for module in ("cdw", "cdw.cli"):
        result = subprocess.run([sys.executable, "-m", module, "--version"], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == __version__
