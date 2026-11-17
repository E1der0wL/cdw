
from cdw.i18n import tr
from qtpy.QtCore import Qt
from cdw.ui.pages.base import BasePage, PageId, PageSpec
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole
from cdw.ui.widgets import CdwLabel


class ProfilePage(BasePage):

    TEXT = {
        'profile': tr('pages.profile.title'),
        'profile_body': tr('ui.pages.profile.text.profile_body'),
        'profile_values': tr('ui.pages.profile.text.profile_values'),
    }

    SPEC = PageSpec(PageId.PROFILE, TEXT['profile'], TEXT['profile'], TEXT['profile_body'], IconName.PROFILE)

    def __init__(self, config, parent=None, *, theme=None):
        self.config = config
        super().__init__(parent, theme=theme)
        self.build_content()

    def build_content(self):
        text = CdwLabel(ProfilePage.TEXT['profile_values'].format(directory=self.config.data_directory), padding_role=LayoutRole.SECTION, theme=self.theme)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content.addWidget(text)
        self.content.addStretch()
