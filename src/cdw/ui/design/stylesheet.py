"""QSS generation. All color and dimension choices come from StyleSpec."""

from cdw.ui.design.spec import ButtonVariant, ResolvedStyle, TextRole, VisualState

STATE_SELECTORS = {
    VisualState.NORMAL: "", VisualState.HOVER: ":hover", VisualState.SELECTED: ":checked",
    VisualState.SELECTED_HOVER: ":checked:hover", VisualState.DISABLED: ":disabled",
}
BASE_QSS = """
QWidget {{ color: {text}; }}
QMainWindow, QDialog, QWidget[cdwSurface="window"] {{ background: {window}; }}
QWidget[cdwSurface="panel"] {{ background: {sidebar}; }}
QWidget[cdwSurface="popup"] {{ background: {surface}; border: none; border-radius: {popup_radius}px; }}
QFrame[cdwSurface="card"] {{ background: {secondary_surface}; border: {border_width}px solid {border}; border-radius: {radius}px; }}
QWidget[cdwSurface="section"] {{ background: transparent; border: none; }}
QLabel {{ background: transparent; border: none; }}
QLabel:disabled {{ color: {disabled_text}; }}
QPushButton {{ border: {border_width}px solid {border}; border-radius: {radius}px;
    padding: {padding_y}px {padding_x}px; }}
QPushButton[cdwVariant="navigation"] {{ text-align: left; }}
QPushButton[cdwCompact="true"] {{ text-align: center; padding-left: 0; padding-right: 0; }}
QPushButton[cdwVariant="field_action"] {{ border: none; border-radius: {underline_radius}px; padding: 0 {gap_small}px; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {surface}; color: {text};
    border: none; border-bottom: {border_width}px solid {border}; border-radius: {underline_radius}px;
    padding: {padding_y}px {padding_x}px; selection-background-color: {selection}; selection-color: {selection_text}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {disabled_text}; background: {disabled_bg}; }}
QComboBox {{ padding-right: {field_button_width}px; }}
QComboBox:hover, QComboBox:on {{ border-color: {accent}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
    width: {field_button_width}px; background: transparent; border: none;
    border-radius: {underline_radius}px; }}
QComboBox::drop-down:hover, QComboBox::drop-down:on {{ background: {hover}; }}
QComboBox::drop-down:disabled {{ background: {disabled_bg}; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QSpinBox, QDoubleSpinBox {{ padding-right: {field_button_width}px; }}
QSpinBox > QLineEdit, QDoubleSpinBox > QLineEdit {{ background: transparent; border: none; padding: 0; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ subcontrol-origin: padding;
    width: {field_button_width}px; background: transparent; border: none; border-radius: {underline_radius}px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {hover}; }}
QSpinBox::up-button:disabled, QSpinBox::down-button:disabled,
QDoubleSpinBox::up-button:disabled, QDoubleSpinBox::down-button:disabled {{ background: transparent; }}
QSpinBox::up-arrow, QSpinBox::down-arrow, QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: {surface}; color: {text};
    border: none; border-radius: {popup_radius}px; padding: {gap_small}px; outline: none;
    selection-background-color: {selection}; selection-color: {selection_text}; }}
QComboBox QAbstractItemView::item {{ padding: {padding_y}px {padding_x}px; border-radius: {radius}px; }}
QComboBox QAbstractItemView::item:selected {{ background: {selection}; color: {selection_text}; }}
QPlainTextEdit {{ background: {secondary_surface}; color: {text}; border: {border_width}px solid {border};
    border-radius: {radius}px; padding: {padding_y}px; selection-background-color: {selection}; selection-color: {selection_text}; }}
QTableView, QTreeWidget {{ background: {surface}; color: {text}; alternate-background-color: {subtle};
    border: {border_width}px solid {border}; border-radius: {radius}px; gridline-color: {subtle}; selection-background-color: {selection}; selection-color: {selection_text}; }}
QTableView {{ padding: {radius}px; }}
QTableView[cdwEmbedded="true"] {{ border: none; border-radius: 0; padding: 0; }}
QTreeWidget {{ background: transparent; border: none; padding: {section_padding}px; }}
QTreeWidget[cdwInsetTree="true"] {{ padding: 0; }}
QHeaderView::section {{ background: {subtle}; color: {muted}; border: none;
    border-bottom: {border_width}px solid {border}; padding: {padding_y}px; }}
QTreeWidget QHeaderView {{ background: {window}; }}
QTreeWidget QHeaderView::section {{ background: {window}; color: {text}; border: none; padding: 0; min-height: {control_height}px; }}
QTableCornerButton::section {{ background: {subtle}; border: none; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ background: transparent; border: none; }}
QTabBar::tab {{ padding: {padding_y}px {padding_x}px; background: transparent; color: {muted};
    margin: 0; border: {border_width}px solid transparent; border-bottom: none;
    border-radius: {underline_radius}px; }}
QTabBar::tab:selected {{ color: {accent_text}; }}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tear {{ image: none; width: 0; border: none; }}
QTabBar QPushButton[cdwVariant="field_action"] {{ background: transparent; margin: 0; padding: 0; }}
QTabBar QToolButton {{ background: {secondary_surface}; color: {text}; border: none;
    border-radius: {radius}px; margin: {border_width}px; }}
QStatusBar {{ background: {log_surface}; border-top: {border_width}px solid {border}; }}
QStatusBar::item {{ border: none; }}
QStatusBar[cdwClickable="true"]:hover {{ background: {log_surface}; }}
QStatusBar[cdwClickable="true"]:focus {{ border-top-color: {accent}; }}
QSplitter::handle {{ background: {border}; }}
QSplitter[cdwSectionSplit="true"]::handle {{ background: {window}; }}
QSplitter[cdwSectionSplit="true"]::handle:hover {{ background: {window}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {subtle}; width: {gap}px; margin: 0; }}
QScrollBar:horizontal {{ background: {subtle}; height: {gap}px; margin: 0; }}
QScrollBar::handle {{ background: {border}; border-radius: {radius}px; min-width: {gap}px; min-height: {gap}px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {tooltip_bg}; color: {tooltip_text}; border: none; border-radius: {popup_radius}px; padding: {padding_y}px; }}
QMenu {{ background: {surface}; color: {text}; border: none; border-radius: {popup_radius}px; }}
QMenu::item:selected {{ background: {selection}; color: {selection_text}; }}
"""


def build_stylesheet(style: ResolvedStyle) -> str:
    tokens = dict(style.colors)
    tokens.update(style.metrics)
    # "border" is a semantic color; geometry has its own explicit name.
    tokens["border"] = style.colors["border"]
    tokens["border_width"] = style.metric("border")
    sections = [BASE_QSS.format(**tokens)]
    for role in TextRole:
        color = style.colors[style.spec.fonts[role].color]
        sections.append(f'QLabel[cdwTextRole="{role.value}"] {{ color: {color}; }}')
    for variant in ButtonVariant:
        for state, selector in STATE_SELECTORS.items():
            background, foreground, border = style.button_colors(variant, state)
            sections.append(f'QPushButton[cdwVariant="{variant.value}"]{selector} '
                            f'{{ background: {background}; color: {foreground}; border-color: {border}; }}')
    sections.append('QPushButton[cdwVariant="field_action"] { border: none; }')
    return "\n".join(sections)
