from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cdw import __version__
from cdw.branding import APP_FULL_NAME, APP_NAME
from cdw.config import PROFILE_LOCK_NAME, QT_BINDING, build_config
from cdw.domain.contracts import CdwError
from cdw.infrastructure.profile_lock import ProfileLock

ORGANIZATION_NAME = APP_NAME
STARTUP_ERROR_TITLE = f"{APP_NAME}를 시작하지 못했습니다."
GUI_DESCRIPTION = APP_FULL_NAME


def run_gui(data_directory: Path | None = None) -> int:
    os.environ["QT_API"] = QT_BINDING
    from qtpy.QtWidgets import QApplication, QMessageBox
    from cdw.application.service import ApplicationService
    from cdw.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName(ORGANIZATION_NAME)
    config = build_config(data_directory)
    lock = ProfileLock(config.data_directory / PROFILE_LOCK_NAME)
    try:
        lock.acquire()
        service = ApplicationService(config)
        window = MainWindow(service)
        window.show()
        return application.exec()
    except (CdwError, OSError) as exc:
        details = exc.details if isinstance(exc, CdwError) else ""
        QMessageBox.critical(None, STARTUP_ERROR_TITLE, f"{exc}\n{details}".strip())
        return 1
    finally:
        lock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=GUI_DESCRIPTION)
    parser.add_argument("--data-dir", type=Path, help="프리셋과 실행 이력을 저장할 폴더")
    parser.add_argument("--version", action="version", version=__version__)
    arguments = parser.parse_args(argv)
    return run_gui(arguments.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
