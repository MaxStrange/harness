"""Inline approval (P8, SEC1): exact detail, plain-language summary, approve / edit / reject."""

from __future__ import annotations

import html
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from harness.config import ThemeConfig
from harness.skills.base import ApprovalDecision
from harness.skills.runner import PendingApproval
from harness.ui.markdown import render_diff, render_plain

Resolver = Callable[[str, ApprovalDecision], bool]


class ApprovalWidget(QFrame):
    _summary_ready = Signal(str)

    def __init__(self, pending: PendingApproval, theme: ThemeConfig, resolve: Resolver) -> None:
        super().__init__()
        self.setObjectName("approvalBubble")
        self.pending = pending
        self.resolve = resolve
        self.theme = theme
        request = pending.request
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        title = QLabel(
            f"<b>Approval needed:</b> {html.escape(request.title)} <span style='color:{theme.text_muted}'>({html.escape(request.skill)})</span>"
        )
        title.setObjectName("bubbleText")
        title.setWordWrap(True)
        layout.addWidget(title)

        if request.reason:
            reason = QLabel(
                f"<span style='color:{theme.warning}'>{html.escape(request.reason)}</span>"
            )
            reason.setObjectName("bubbleText")
            reason.setWordWrap(True)
            layout.addWidget(reason)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("bubbleText")
        self.summary_label.setWordWrap(True)
        self.summary_label.setVisible(False)
        layout.addWidget(self.summary_label)
        self._summary_ready.connect(self._show_summary)
        if request.summary is not None:
            self._show_summary(request.summary)
        elif pending.summary_pending:
            self._show_summary("Summarizing...", pending=True)
            pending.summary_listeners.append(self._summary_ready.emit)

        self.detail_view = QTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setFont(QFont("monospace"))
        if request.detail_kind == "diff":
            self.detail_view.setHtml(render_diff(request.detail, theme))
        else:
            self.detail_view.setHtml(render_plain(request.detail, theme))
        self.detail_view.setMaximumHeight(min(320, 40 + 18 * (request.detail.count("\n") + 1)))
        layout.addWidget(self.detail_view)

        self.editor = QPlainTextEdit(request.detail)
        self.editor.setFont(QFont("monospace"))
        self.editor.setVisible(False)
        self.editor.setMaximumHeight(200)
        layout.addWidget(self.editor)

        self.reason_edit = QLineEdit()
        self.reason_edit.setPlaceholderText("Optional reason for rejecting (sent to the model)")
        layout.addWidget(self.reason_edit)

        row = QHBoxLayout()
        self.approve_button = QPushButton("Approve")
        self.approve_button.setObjectName("accent")
        self.approve_button.clicked.connect(self._approve)
        row.addWidget(self.approve_button)
        self.edit_button = QPushButton("Edit, then approve")
        self.edit_button.setObjectName("warning")
        self.edit_button.setVisible(request.editable_field is not None)
        self.edit_button.clicked.connect(self._edit)
        row.addWidget(self.edit_button)
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("danger")
        self.reject_button.clicked.connect(self._reject)
        row.addWidget(self.reject_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.outcome_label = QLabel()
        self.outcome_label.setObjectName("bubbleText")
        self.outcome_label.setVisible(False)
        layout.addWidget(self.outcome_label)
        self.decided = False
        self._editing = False

    def _show_summary(self, text: str, pending: bool = False) -> None:
        offline = text.startswith("Summarizer model offline") or pending
        color = self.theme.text_muted if offline else self.theme.text
        self.summary_label.setText(f"<i style='color:{color}'>{html.escape(text)}</i>")
        self.summary_label.setVisible(True)

    def _edit(self) -> None:
        if not self._editing:
            self._editing = True
            self.editor.setVisible(True)
            self.detail_view.setVisible(False)
            self.edit_button.setText("Approve edited")
            self.editor.setFocus()
            return
        field = self.pending.request.editable_field
        edited = {field: self.editor.toPlainText()} if field else None
        self._finish(ApprovalDecision.approve(edited), f"Approved with edits ({field})")

    def _approve(self) -> None:
        self._finish(ApprovalDecision.approve(), "Approved")

    def _reject(self) -> None:
        reason = self.reason_edit.text().strip() or None
        self._finish(
            ApprovalDecision.reject(reason), "Rejected" + (f": {reason}" if reason else "")
        )

    def _finish(self, decision: ApprovalDecision, label: str) -> None:
        if self.decided:
            return
        self.decided = True
        self.resolve(self.pending.id, decision)
        for widget in (
            self.approve_button,
            self.edit_button,
            self.reject_button,
            self.reason_edit,
            self.editor,
        ):
            widget.setVisible(False)
        self.detail_view.setVisible(True)
        color = self.theme.accent if decision.approved else self.theme.error
        self.outcome_label.setText(f"<b style='color:{color}'>{html.escape(label)}</b>")
        self.outcome_label.setVisible(True)
        self.setStyleSheet(f"QFrame#approvalBubble {{ border-color: {color}; }}")

    def cancel(self, note: str = "No longer pending") -> None:
        if not self.decided:
            self.decided = True
            for widget in (
                self.approve_button,
                self.edit_button,
                self.reject_button,
                self.reason_edit,
                self.editor,
            ):
                widget.setVisible(False)
            self.outcome_label.setText(
                f"<i style='color:{self.theme.text_muted}'>{html.escape(note)}</i>"
            )
            self.outcome_label.setVisible(True)
            self.setStyleSheet("QFrame#approvalBubble { border-color: gray; }")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
