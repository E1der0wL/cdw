from __future__ import annotations

from cdw.i18n import tr

import argparse
import os
import sys
from pathlib import Path

from cdw import __version__
from cdw.branding import APP_FULL_NAME, APP_NAME
from cdw.config import QT_BINDING, build_config
from cdw.domain.contracts import CdwError

ORGANIZATION_NAME = APP_NAME
STARTUP_ERROR_TITLE = tr('startup.startup_error_title').format(app_name=APP_NAME)
GUI_DESCRIPTION = APP_FULL_NAME


def run_gui(data_directory: Path | None = None) -> int:
    os.environ["QT_API"] = QT_BINDING
    from qtpy.QtWidgets import QApplication, QMessageBox
    from cdw.server.client import RemoteApplicationService
    from cdw.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName(ORGANIZATION_NAME)
    config = build_config(data_directory)
    try:
        service = RemoteApplicationService(config)
        window = MainWindow(service)
        window.show()
        return application.exec()
    except (CdwError, OSError) as exc:
        details = exc.details if isinstance(exc, CdwError) else ""
        QMessageBox.critical(None, STARTUP_ERROR_TITLE, f"{exc}\n{details}".strip())
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=GUI_DESCRIPTION)
    parser.add_argument("--data-dir", type=Path, help=tr('startup.main'))
    parser.add_argument("--version", action="version", version=__version__)
    arguments = parser.parse_args(argv)
    return run_gui(arguments.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
