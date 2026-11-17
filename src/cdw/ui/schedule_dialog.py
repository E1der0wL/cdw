"""Schedule editor chooses policy; server-side contracts validate and execute it."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from cdw.domain.contracts import CdwError
from cdw.domain.schedules import ScheduleDefinition, TriggerKind, TriggerSpec, WorkflowDefinition
from cdw.infrastructure.schedule_clock import ScheduleClock
from cdw.i18n import tr
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.schedule_presentation import SchedulePresentation
from cdw.ui.widgets import (
    CdwButton, CdwComboBox, CdwDialog, CdwDialogButtonBox, CdwDoubleSpinBox,
    CdwFormLayout, CdwHBoxLayout, CdwLabel, CdwLineEdit, CdwScrollArea,
    CdwSection, CdwSectionGroup, CdwVBoxLayout, CdwWidget,
)


class ScheduleDialog(CdwDialog):
    INITIAL_DELAY_SECONDS = 300
    DEFAULT_INTERVAL_VALUE = 5
    DEFAULT_WALL_TIME = "09:00:00"
    INTERVAL_UNITS = ((tr("schedule.dialog.seconds"), 1), (tr("schedule.dialog.minutes"), 60), (tr("schedule.dialog.hours"), 3600))
    TRIGGER_LABELS = {
        TriggerKind.INTERVAL: tr("schedule.dialog.interval"), TriggerKind.ONCE: tr("schedule.dialog.once"),
        TriggerKind.WEEKLY: tr("schedule.dialog.weekly"),
    }

    def __init__(self, service, application_service, task_service, entry=None, parent=None, *, theme=None):
        super().__init__(parent, theme=theme or getattr(parent, "theme", None))
        self.service = service
        self.application_service = application_service
        self.task_service = task_service
        self.original = entry
        self.saved_schedule = None
        self.original_workflow = service.get_workflow(entry.definition.workflow_id) if entry else None
        self.setWindowTitle(tr("schedule.dialog.edit_title") if entry else tr("schedule.dialog.title"))
        layout = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        scroll = CdwScrollArea(theme=self.theme)
        body = CdwWidget(theme=self.theme)
        content = CdwVBoxLayout(body, role=LayoutRole.FLUSH, theme=self.theme)
        sections = CdwSectionGroup(theme=self.theme)
        content.addWidget(sections)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        flow = CdwSection(tr("schedule.dialog.flow"), theme=self.theme)
        flow_form = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        flow.content.addLayout(flow_form)
        self.name_edit = CdwLineEdit(theme=self.theme)
        self.name_edit.setObjectName("schedule_name")
        self.preset_combo = CdwComboBox(theme=self.theme)
        self.preset_combo.setObjectName("schedule_preset")
        for preset in application_service.list_presets():
            self.preset_combo.addItem(preset.name, preset.id)
        self.task_combo = CdwComboBox(theme=self.theme)
        self.task_combo.setObjectName("schedule_task")
        self.task_combo.addItem(tr("schedule.ui.collect_only"), None)
        for task in task_service.list_tasks():
            self.task_combo.addItem(task.name, task.id)
        self.enabled_combo = CdwComboBox(theme=self.theme)
        self.enabled_combo.addItem(tr("schedule.ui.enabled"), True)
        self.enabled_combo.addItem(tr("schedule.ui.paused"), False)
        for label, field in ((tr("schedule.dialog.name"), self.name_edit), (tr("schedule.ui.preset"), self.preset_combo),
                             (tr("schedule.ui.task"), self.task_combo), (tr("schedule.dialog.enabled"), self.enabled_combo)):
            flow_form.addRow(label, field)
        sections.add_section(flow)

        timing = CdwSection(tr("schedule.dialog.timing"), theme=self.theme)
        self.timing_form = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        timing.content.addLayout(self.timing_form)
        self.trigger_combo = CdwComboBox(theme=self.theme)
        self.trigger_combo.setObjectName("schedule_trigger")
        for kind, label in self.TRIGGER_LABELS.items():
            self.trigger_combo.addItem(label, kind)
        self.timezone_edit = CdwLineEdit(TriggerSpec.DEFAULT_TIMEZONE, theme=self.theme)
        self.timezone_edit.setObjectName("schedule_timezone")
        self.period_widget = CdwWidget(theme=self.theme)
        period = CdwHBoxLayout(self.period_widget, role=LayoutRole.ROW, theme=self.theme)
        self.interval_spin = CdwDoubleSpinBox(theme=self.theme)
        self.interval_spin.setDecimals(0)
        self.interval_spin.setRange(1, TriggerSpec.MAX_INTERVAL_SECONDS)
        self.interval_spin.setValue(self.DEFAULT_INTERVAL_VALUE)
        self.interval_unit = CdwComboBox(theme=self.theme)
        for label, multiplier in self.INTERVAL_UNITS:
            self.interval_unit.addItem(label, multiplier)
        self.interval_unit.setCurrentIndex(1)
        period.addWidget(self.interval_spin, 1)
        period.addWidget(self.interval_unit)
        self.anchor_edit = CdwLineEdit(theme=self.theme)
        self.once_edit = CdwLineEdit(theme=self.theme)
        self.clock_edit = CdwLineEdit(self.DEFAULT_WALL_TIME, theme=self.theme)
        self.anchor_edit.setObjectName("schedule_anchor")
        self.once_edit.setObjectName("schedule_once")
        self.clock_edit.setObjectName("schedule_clock")
        for field in (self.anchor_edit, self.once_edit):
            field.setPlaceholderText(tr("schedule.dialog.datetime_hint"))
        self.clock_edit.setPlaceholderText(tr("schedule.dialog.time_hint"))
        self.weekdays_widget = CdwWidget(theme=self.theme)
        weekdays = CdwHBoxLayout(self.weekdays_widget, role=LayoutRole.ROW, theme=self.theme)
        self.weekday_buttons = []
        for day, label in enumerate(SchedulePresentation.WEEKDAYS):
            button = CdwButton(label, theme=self.theme)
            button.setCheckable(True)
            button.setChecked(True)
            button.setObjectName(f"schedule_weekday_{day}")
            self.weekday_buttons.append(button)
            weekdays.addWidget(button)
        weekdays.addStretch()
        self.grace_spin = CdwDoubleSpinBox(theme=self.theme)
        self.grace_spin.setDecimals(0)
        self.grace_spin.setRange(0, ScheduleDefinition.MAX_GRACE_SECONDS)
        self.grace_spin.setValue(ScheduleDefinition.DEFAULT_GRACE_SECONDS)
        for label, field in ((tr("schedule.ui.trigger"), self.trigger_combo), (tr("schedule.dialog.timezone"), self.timezone_edit),
                             (tr("schedule.dialog.period"), self.period_widget), (tr("schedule.dialog.anchor"), self.anchor_edit),
                             (tr("schedule.dialog.once_at"), self.once_edit), (tr("schedule.dialog.clock"), self.clock_edit),
                             (tr("schedule.dialog.weekdays"), self.weekdays_widget), (tr("schedule.dialog.grace"), self.grace_spin)):
            self.timing_form.addRow(label, field)
        help_label = CdwLabel(tr("schedule.dialog.policy_help"), role=TextRole.MUTED, theme=self.theme)
        help_label.setWordWrap(True)
        timing.content.addWidget(help_label)
        sections.add_section(timing)
        self.feedback = CdwLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = CdwDialogButtonBox(theme=self.theme)
        self.save_button = buttons.add_action(tr("ui.preset_dialog.labels.update") if entry else tr("ui.preset_dialog.labels.save"),
            CdwDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        self.save_button.setObjectName("save_schedule")
        buttons.add_action(tr("common.cancel"), CdwDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.trigger_combo.currentIndexChanged.connect(self._trigger_changed)
        initial = datetime.now(timezone.utc) + timedelta(seconds=self.INITIAL_DELAY_SECONDS)
        local = initial.astimezone(ScheduleClock.zone(TriggerSpec.DEFAULT_TIMEZONE)).strftime(SchedulePresentation.TIME_FORMAT)
        self.anchor_edit.setText(local)
        self.once_edit.setText(local)
        if entry:
            self._load(entry)
        if not self.preset_combo.count():
            self.feedback.setText(tr("schedule.dialog.no_presets"))
            self.save_button.setEnabled(False)
        self._trigger_changed()

    def _load(self, entry):
        definition = entry.definition
        trigger = definition.trigger
        self.name_edit.setText(definition.name)
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(self.original_workflow.preset_id))
        self.task_combo.setCurrentIndex(self.task_combo.findData(self.original_workflow.task_id))
        self.enabled_combo.setCurrentIndex(self.enabled_combo.findData(definition.enabled))
        self.trigger_combo.setCurrentIndex(self.trigger_combo.findData(trigger.kind))
        self.timezone_edit.setText(trigger.timezone)
        if trigger.kind is TriggerKind.INTERVAL:
            self.interval_unit.setCurrentIndex(0)
            self.interval_spin.setValue(trigger.interval_seconds)
            self.anchor_edit.setText(SchedulePresentation.timestamp(trigger.anchor_at, trigger.timezone))
        elif trigger.kind is TriggerKind.ONCE:
            self.once_edit.setText(SchedulePresentation.timestamp(trigger.at, trigger.timezone))
        else:
            self.clock_edit.setText(trigger.wall_time)
            for day, button in enumerate(self.weekday_buttons):
                button.setChecked(day in trigger.weekdays)
        self.grace_spin.setValue(definition.misfire_grace_seconds)

    def _trigger_changed(self):
        kind = TriggerKind(self.trigger_combo.currentData())
        for field in (self.period_widget, self.anchor_edit):
            self.timing_form.setRowVisible(field, kind is TriggerKind.INTERVAL)
        self.timing_form.setRowVisible(self.once_edit, kind is TriggerKind.ONCE)
        for field in (self.clock_edit, self.weekdays_widget):
            self.timing_form.setRowVisible(field, kind is TriggerKind.WEEKLY)

    def _save(self):
        try:
            kind = TriggerKind(self.trigger_combo.currentData())
            zone = self.timezone_edit.text().strip()
            fields = {"kind": kind, "timezone": zone}
            if kind is TriggerKind.INTERVAL:
                fields.update(interval_seconds=self.interval_spin.value() * self.interval_unit.currentData(),
                              anchor_at=ScheduleClock.local_datetime(self.anchor_edit.text().strip(), zone).isoformat())
            elif kind is TriggerKind.ONCE:
                fields["at"] = ScheduleClock.local_datetime(self.once_edit.text().strip(), zone).isoformat()
            else:
                fields.update(wall_time=self.clock_edit.text().strip(),
                              weekdays=tuple(day for day, button in enumerate(self.weekday_buttons) if button.isChecked()))
            workflow = WorkflowDefinition(
                self.original_workflow.id if self.original_workflow else str(uuid4()),
                self.name_edit.text(), self.preset_combo.currentData(), self.task_combo.currentData())
            schedule = ScheduleDefinition(
                self.original.definition.id if self.original else str(uuid4()), self.name_edit.text(),
                workflow.id, TriggerSpec(**fields), bool(self.enabled_combo.currentData()), int(self.grace_spin.value()))
            self.saved_schedule = self.service.save_bundle(workflow, schedule)
        except (CdwError, ValueError, TypeError, OSError) as exc:
            details = f"{exc}\n{exc.details}" if isinstance(exc, CdwError) else str(exc)
            self.feedback.setText(tr("schedule.ui.failed").format(details=details.strip()))
            return
        self.accept()
