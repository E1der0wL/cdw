"""Run on the deployment machine: python scripts/check_environment.py."""

import importlib
import json
import platform
import sys
from importlib.metadata import requires, version

from packaging.requirements import Requirement

EXPECTED_PYTHON = (3, 12, 14)
MINIMUM_GLIBC = (2, 28)
SUPPORTED_ARCHITECTURES = {"x86_64", "amd64"}
IMPORTS = ("PySide6.QtCore", "PySide6.QtWidgets", "qtpy", "duckdb", "polars", "pyarrow", "pytz")
PROJECT_NAME = "cdw-desktop"


def main() -> int:
    problems = []
    if sys.version_info[:3] != EXPECTED_PYTHON:
        problems.append(f"Python {EXPECTED_PYTHON} required; found {sys.version_info[:3]}")
    if platform.machine().lower() not in SUPPORTED_ARCHITECTURES:
        problems.append(f"Unsupported architecture: {platform.machine()}")
    libc = platform.libc_ver()
    if platform.system() == "Linux" and (libc[0] != "glibc" or tuple(map(int, libc[1].split(".")[:2])) < MINIMUM_GLIBC):
        problems.append(f"glibc >= {MINIMUM_GLIBC} required; found {libc}")
    packages = {}
    for text in requires(PROJECT_NAME) or ():
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        installed = version(requirement.name)
        packages[requirement.name] = installed
        if installed not in requirement.specifier:
            problems.append(f"{requirement.name}: {installed} does not match {requirement.specifier}")
    for module in IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:
            problems.append(f"{module}: {exc}")
    print(json.dumps({"python": platform.python_version(), "platform": platform.platform(), "libc": libc,
                      "packages": packages, "problems": problems}, ensure_ascii=False, indent=2))
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
