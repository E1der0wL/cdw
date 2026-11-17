from __future__ import annotations

from cdw.i18n import tr

from datetime import datetime
from qtpy.QtWidgets import QButtonGroup

from cdw import __version__
from cdw.branding import APP_NAME, APP_FULL_NAME
from cdw.server.client import RemoteApplicationService
from cdw.domain.contracts import CdwError, ErrorCode, RunState
from cdw.ui.controller import Controller, Operation
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.log_dialog import LogDialog
from cdw.ui.pages.base import PageId
from cdw.ui.pages.registry import build_pages
from cdw.ui.widgets import (
    CdwHBoxLayout, CdwLabel, CdwLogBar, CdwMainWindow,
    CdwNavigationButton, CdwScrollArea, CdwSidebar, CdwSidebarSplitter,
    CdwStackedWidget, CdwTitleArea, CdwVBoxLayout, CdwWidget,
)


class MainWindow(CdwMainWindow):

    TEXT = {
        "title": tr('app.window_title').format(app_name=APP_NAME, app_full_name=APP_FULL_NAME), "brand": APP_NAME, "caption": APP_FULL_NAME.upper(),
        "ready": tr('ui.main_window.text.ready'), "log_hint": tr('ui.main_window.text.log_hint'),
        "committing": tr('ui.main_window.text.committing'),
    }

    STATUS_TIME_FORMAT = "%H:%M:%S"

    def __init__(self, service: RemoteApplicationService):
        super().__init__(theme=ThemeManager.for_service(service))
        self.service = service
        self.task_service = service.tasks
        self.controller = Controller(service, self.task_service)
        self._closing = False
        self._closed = False
        self._current_page = None
        self.setWindowTitle(MainWindow.TEXT["title"])
        self._build()
        self.controller.completed.connect(self._completed)
        self.controller.failed.connect(self._failed)
        self.controller.progress.connect(self._progress)
        self.controller.submit(Operation.BOOT, self.service.recover)
        if self.theme.load_warning:
            self.log(self.theme.load_warning)

    def _build(self):
        root = CdwWidget(surface="window", theme=self.theme)
        layout = CdwHBoxLayout(root, role=LayoutRole.FLUSH, theme=self.theme)
        self.sidebar = CdwSidebar(theme=self.theme)
        sidebar_layout = CdwVBoxLayout(self.sidebar.content_widget, role=LayoutRole.FLUSH, theme=self.theme)
        self.sidebar_title = CdwTitleArea(MainWindow.TEXT["brand"], MainWindow.TEXT["caption"], title_role=TextRole.BRAND,
                                         subtitle_role=TextRole.CAPTION, theme=self.theme)
        self.sidebar_title.header.insertWidget(0, self.sidebar.toggle_button)
        sidebar_layout.addWidget(self.sidebar_title)
        self.sidebar.register_title(self.sidebar_title)
        navigation = CdwVBoxLayout(role=LayoutRole.SIDEBAR, theme=self.theme)
        sidebar_layout.addLayout(navigation, 1)
        self.sidebar.register_navigation_layout(navigation)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        self.pages = CdwStackedWidget(theme=self.theme)
        self._page_views = {}
        self._page_hosts = {}
        registrations = build_pages(self.service, self.task_service, self.controller, self.theme)
        for bottom in (False, True):
            if bottom:
                navigation.addStretch()
            for registration in registrations:
                if registration.bottom != bottom:
                    continue
                page = registration.page
                spec = page.SPEC
                self._page_views[spec.id] = page
                host = CdwScrollArea(theme=self.theme)
                host.setWidget(page)
                self._page_hosts[spec.id] = host
                self.pages.addWidget(host)
                button = CdwNavigationButton(spec.navigation, icon=spec.icon, theme=self.theme)
                self.nav_group.addButton(button)
                self.nav_buttons[spec.id] = button
                navigation.addWidget(button)
                self.sidebar.register_navigation(button)
                button.clicked.connect(lambda _checked=False, page_id=spec.id: self.show_page(page_id))
                page.navigate_requested.connect(self.show_page)
                page.log_requested.connect(self.log)
                page.log_details_requested.connect(self.append_log_details)
                page.error_requested.connect(self._show_error)
        footer = CdwLabel(tr('app.footer').format(version=__version__), role=TextRole.CAPTION, theme=self.theme)
        navigation.addWidget(footer)
        self.sidebar.register_expanded_widget(footer)
        self.sidebar.apply_style(self.theme.current)
        self.shell_splitter = CdwSidebarSplitter(self.sidebar, self.pages, theme=self.theme)
        layout.addWidget(self.shell_splitter)
        self.setCentralWidget(root)
        self.log_dialog = LogDialog(self, theme=self.theme)
        self.log_bar = CdwLogBar(MainWindow.TEXT["ready"], self, theme=self.theme)
        self.log_bar.setToolTip(MainWindow.TEXT["log_hint"])
        self.log_bar.setAccessibleName(MainWindow.TEXT["log_hint"])
        self.log_bar.activated.connect(self.log_dialog.reveal)
        self.setStatusBar(self.log_bar)
        self.show_page(PageId.HOME)

    def page(self, page_id: PageId):
        return self._page_views[page_id]

    def show_page(self, page_id: PageId):
        target = self.page(page_id)
        if target is self._current_page:
            return
        if self._current_page is not None:
            self._current_page.on_leave()
        self.pages.setCurrentWidget(self._page_hosts[page_id])
        self.nav_buttons[page_id].setChecked(True)
        self._current_page = target
        try:
            target.on_enter()
        except CdwError as error:
            self._show_error(error)

    def _progress(self, progress):
        self.log(MainWindow.TEXT["committing"] if progress.state is RunState.SAVING else progress.message)

    def _completed(self, operation, value):
        if operation == Operation.TASK and not self._closing:
            self.log(tr('ui.pages.sql.text.task_done').format(name=value.task_name, seconds=value.elapsed_seconds))
            for output in (value.stdout, value.stderr):
                if output:
                    self.append_log_details(output)

    def _failed(self, operation, error):
        self._show_error(error)

    def _show_error(self, error: CdwError):
        self.log(f"{error}\n{error.details}".strip())
        if not self._closing and error.code is not ErrorCode.CANCELLED:
            self.log_dialog.reveal()

    def log(self, message):
        first_line = message.splitlines()[0] if message else ""
        self.log_bar.message_label.setText(first_line)
        self.log_bar.message_label.setToolTip(message)
        self.log_dialog.log_view.appendPlainText(f"[{datetime.now().strftime(self.STATUS_TIME_FORMAT)}] {message}")

    def append_log_details(self, message):
        self.log_dialog.log_view.appendPlainText(message)

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self._closing = True
        self.service.detach()
        self.controller.shutdown()
        self.log_dialog.close()
        self._closed = True
        event.accept()
