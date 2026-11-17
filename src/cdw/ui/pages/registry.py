"""Navigation ordering and dependency injection for the persistent page set."""

from dataclasses import dataclass

from cdw.ui.pages.base import BasePage
from cdw.ui.pages.home import HomePage
from cdw.ui.pages.profile import ProfilePage
from cdw.ui.pages.scheduler import SchedulerPage
from cdw.ui.pages.settings import SettingsPage
from cdw.ui.pages.sql import SqlPage
from cdw.ui.pages.tasks import TasksPage


@dataclass(frozen=True)
class PageRegistration:
    page: BasePage
    bottom: bool = False


def build_pages(service, task_service, controller, theme):
    home = HomePage(service, controller, theme=theme)
    sql = SqlPage(service, task_service, controller, theme=theme)
    tasks = TasksPage(task_service, controller=controller, theme=theme)
    sql.presets_changed.connect(home.refresh)
    controller.state_changed.connect(lambda state: tasks.set_busy(state.busy))
    return (
        PageRegistration(home),
        PageRegistration(sql),
        PageRegistration(SchedulerPage(service.schedules, service, task_service, theme=theme)),
        PageRegistration(tasks),
        PageRegistration(SettingsPage(service.config, theme=theme), bottom=True),
        PageRegistration(ProfilePage(service.config, theme=theme), bottom=True),
    )
