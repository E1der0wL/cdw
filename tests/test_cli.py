import json
import subprocess
import sys


def invoke(directory, *arguments):
    return subprocess.run(
        [sys.executable, "-m", "cdw.cli", "--data-dir", str(directory), *arguments],
        capture_output=True, encoding="utf-8", timeout=30,
    )


def test_cli_registration_collection_query_and_error(tmp_path):
    demo = invoke(tmp_path, "demo")
    assert demo.returncode == 0, demo.stderr
    preset = json.loads(demo.stdout)
    preset["name"] = "CLI registered preset"
    preset_file = tmp_path / "preset.json"
    preset_file.write_text(json.dumps(preset), encoding="utf-8")
    registration = invoke(tmp_path, "register", str(preset_file))
    assert registration.returncode == 0, registration.stderr
    result = invoke(tmp_path, "collect", preset["id"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["inserted_rows"] == 5
    query = invoke(tmp_path, "query", preset["id"], "--sql", "SELECT count(*) FROM source.host_inventory")
    assert query.returncode == 0, query.stderr
    assert json.loads(query.stdout)["rows"] == [[5]]
    bad = invoke(tmp_path, "query", preset["id"], "--sql", "DROP TABLE source.host_inventory")
    assert bad.returncode == 1
    assert json.loads(bad.stderr)["error"] == "invalid_query"
