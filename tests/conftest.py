import os

import pytest

os.environ["QT_API"] = "pyside6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def release_qt_clipboard():
    """Release Qt-owned MIME data before Python/PySide interpreter teardown."""
    yield
    from qtpy.QtCore import QCoreApplication, QEvent
    from qtpy.QtWidgets import QApplication

    application = QApplication.instance()
    if application is not None:
        application.clipboard().clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()


@pytest.fixture(autouse=True)
def dispose_closed_application_windows():
    """QWidget.close only hides windows; dispose completed test owners explicitly."""
    yield
    from qtpy.QtCore import QCoreApplication, QEvent
    from qtpy.QtWidgets import QApplication

    application = QApplication.instance()
    if application is None:
        return
    from cdw.ui.main_window import MainWindow

    for widget in application.topLevelWidgets():
        if isinstance(widget, MainWindow) and widget._closed:
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
