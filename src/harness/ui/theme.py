"""Theme (UI1): the configured colors become one Qt style sheet."""

from __future__ import annotations

from harness.config import ThemeConfig, UiConfig


def build_stylesheet(ui: UiConfig) -> str:
    t: ThemeConfig = ui.theme
    font = f"font-family: {ui.font_family};" if ui.font_family else ""
    return f"""
    QWidget {{ background: {t.background}; color: {t.text}; font-size: {ui.font_size}pt; {font} }}
    QMainWindow::separator {{ background: {t.border}; width: 3px; height: 3px; }}
    QSplitter::handle {{ background: {t.border}; }}
    QSplitter::handle:hover {{ background: {t.accent}; }}
    QFrame#panel, QWidget#panel {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 6px; }}
    QLabel#panelTitle {{ color: {t.text_muted}; font-weight: bold; padding: 4px 6px; background: transparent; border: none; }}
    QScrollArea {{ border: none; background: {t.background}; }}
    QScrollArea > QWidget > QWidget {{ background: {t.background}; }}
    QScrollBar:vertical {{ background: {t.background}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: {t.background}; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 5px; min-width: 24px; }}
    QPushButton {{ background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 5px; padding: 5px 12px; }}
    QPushButton:hover {{ border-color: {t.accent}; }}
    QPushButton:pressed {{ background: {t.border}; }}
    QPushButton:disabled {{ color: {t.text_muted}; }}
    QPushButton#accent {{ background: {t.accent}; color: {t.accent_text}; font-weight: bold; border: none; }}
    QPushButton#accent:hover {{ background: {t.text}; }}
    QPushButton#danger {{ background: {t.error}; color: white; border: none; }}
    QPushButton#warning {{ background: {t.warning}; color: {t.accent_text}; border: none; }}
    QLineEdit, QPlainTextEdit, QTextEdit {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 5px; padding: 4px; selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {t.accent}; }}
    QListWidget, QTreeView {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 6px; outline: none; }}
    QListWidget::item, QTreeView::item {{ padding: 3px; }}
    QListWidget::item:selected, QTreeView::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QListWidget::item:hover, QTreeView::item:hover {{ background: {t.surface_alt}; }}
    QHeaderView::section {{ background: {t.surface_alt}; color: {t.text_muted}; border: none; padding: 3px; }}
    QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 6px; }}
    QTabBar::tab {{ background: {t.surface}; padding: 5px 10px; border: 1px solid {t.border}; border-bottom: none; border-top-left-radius: 5px; border-top-right-radius: 5px; }}
    QTabBar::tab:selected {{ background: {t.surface_alt}; color: {t.accent}; }}
    QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
    QMenuBar {{ background: {t.surface}; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QMenu {{ background: {t.surface}; border: 1px solid {t.border}; }}
    QToolTip {{ background: {t.surface_alt}; color: {t.text}; border: 1px solid {t.accent}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t.border}; border-radius: 3px; background: {t.surface}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; }}
    QLabel#status {{ color: {t.text_muted}; background: transparent; }}
    QLabel#statusError {{ color: {t.error}; background: transparent; }}
    QLabel#statusOk {{ color: {t.accent}; background: transparent; }}
    QWidget#userBubble {{ background: {t.user_bubble}; border-radius: 8px; }}
    QWidget#assistantBubble {{ background: {t.assistant_bubble}; border-radius: 8px; }}
    QWidget#toolBubble {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
    QWidget#approvalBubble {{ background: {t.surface}; border: 2px solid {t.warning}; border-radius: 8px; }}
    QLabel#bubbleText {{ background: transparent; }}
    QLabel#roleLabel {{ color: {t.text_muted}; font-size: {max(7, ui.font_size - 2)}pt; background: transparent; }}
    """
