"""The new launch name preserves existing user data configuration."""

from cdb.config import build_config
from cdw import APP_NAME


def test_new_data_override_takes_precedence_and_old_override_still_works(monkeypatch, tmp_path):
    assert APP_NAME == "CDW"
    legacy = tmp_path / "existing-profile"
    current = tmp_path / "new-profile"
    explicit = tmp_path / "explicit-profile"
    monkeypatch.setenv("CDB_DATA_DIR", str(legacy))
    monkeypatch.delenv("CDW_DATA_DIR", raising=False)
    assert build_config().data_directory == legacy
    monkeypatch.setenv("CDW_DATA_DIR", str(current))
    assert build_config().data_directory == current
    assert build_config(explicit).data_directory == explicit
