"""Shared page contract; feature state belongs to concrete pages."""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from qtpy.QtCore import Signal

from cdw.i18n import tr
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole
from cdw.ui.widgets import CdwTitleArea, CdwVBoxLayout, CdwWidget


class PageId(StrEnum):
    HOME = "home"
    SQL = "sql"
    SCHEDULER = "scheduler"
    TASKS = "tasks"
    SETTINGS = "settings"
    PROFILE = "profile"


@dataclass(frozen=True)
class PageSpec:
    id: PageId
    navigation: str
    title: str
    description: str
    icon: IconName


class PageMeta(ABCMeta, type(CdwWidget)):
    ABSTRACT_PAGE_MESSAGE = tr("pages.abstract_error")

    def __call__(cls, *args, **kwargs):
        # Shiboken's constructor bypasses ABCMeta's instantiation check.
        if cls.__abstractmethods__:
            raise TypeError(PageMeta.ABSTRACT_PAGE_MESSAGE.format(
                name=cls.__name__, methods=", ".join(sorted(cls.__abstractmethods__))))
        return super().__call__(*args, **kwargs)


class BasePage(CdwWidget, metaclass=PageMeta):
    APPLICATION_EYEBROW = tr("pages.eyebrow")
    SPEC: PageSpec
    navigate_requested = Signal(object)
    log_requested = Signal(str)
    log_details_requested = Signal(str)
    error_requested = Signal(object)

    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme, surface="window")
        self.content = CdwVBoxLayout(self, role=LayoutRole.PAGE, theme=self.theme)
        self.title_area = CdwTitleArea(self.SPEC.title, self.SPEC.description,
                                      eyebrow=self.APPLICATION_EYEBROW, theme=self.theme)
        self.content.addWidget(self.title_area)

    @abstractmethod
    def build_content(self):
        """Build once, after the concrete page has assigned its dependencies."""

    def on_enter(self):
        """Refresh optional page data without rebuilding the page."""

    def on_leave(self):
        """Release optional transient presentation state."""
