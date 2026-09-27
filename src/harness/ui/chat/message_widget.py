"""Chat bubbles: user, assistant (Markdown, streaming), and skill results with handoff buttons."""

from __future__ import annotations

import html
import json
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from harness.config import ThemeConfig
from harness.skills.base import Handoff
from harness.skills.runner import SkillOutcome
from harness.ui.markdown import MarkdownRenderer, render_plain

HandoffCallback = Callable[[Handoff], None]


class Bubble(QFrame):
    def __init__(self, object_name: str, role_text: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 10)
        self._layout.setSpacing(4)
        self.role_label = QLabel(role_text)
        self.role_label.setObjectName("roleLabel")
        self._layout.addWidget(self.role_label)
        self.text_label = QLabel()
        self.text_label.setObjectName("bubbleText")
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.text_label.setOpenExternalLinks(True)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout.addWidget(self.text_label)

    def set_html(self, markup: str) -> None:
        self.text_label.setText(markup)


class UserBubble(Bubble):
    def __init__(self, text: str, theme: ThemeConfig) -> None:
        super().__init__("userBubble", "You")
        self.set_html(f'<div style="white-space:pre-wrap">{html.escape(text)}</div>')


class AssistantBubble(Bubble):
    """Accumulates streamed text and re-renders Markdown on demand."""

    def __init__(self, renderer: MarkdownRenderer) -> None:
        super().__init__("assistantBubble", "Assistant")
        self.renderer = renderer
        self.text = ""
        self.dirty = False

    def append(self, delta: str) -> None:
        self.text += delta
        self.dirty = True

    def set_text(self, text: str) -> None:
        self.text = text
        self.dirty = True
        self.render()

    def render(self) -> None:
        if not self.dirty:
            return
        self.dirty = False
        self.set_html(
            self.renderer.render(self.text)
            if self.text.strip()
            else '<i style="color:gray">...</i>'
        )


class SkillBubble(Bubble):
    """A skill call: shows arguments while running, then the result and its handoff button."""

    def __init__(
        self, call_id: str, skill: str, args: dict, theme: ThemeConfig, on_handoff: HandoffCallback
    ) -> None:
        super().__init__("toolBubble", f"Skill: {skill}")
        self.call_id = call_id
        self.skill = skill
        self.theme = theme
        self.on_handoff = on_handoff
        self.text_label.setText(
            f'<span style="color:{theme.text_muted}">running</span> <code>{html.escape(_short_args(args))}</code>'
        )
        self.detail_label = QLabel()
        self.detail_label.setObjectName("bubbleText")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setVisible(False)
        self._layout.addWidget(self.detail_label)
        self.buttons = QWidget()
        self.buttons.setObjectName("bubbleText")
        row = QHBoxLayout(self.buttons)
        row.setContentsMargins(0, 4, 0, 0)
        self.toggle = QPushButton("Show output")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._toggle_detail)
        row.addWidget(self.toggle)
        self.handoff_button = QPushButton()
        self.handoff_button.setObjectName("accent")
        self.handoff_button.setVisible(False)
        row.addWidget(self.handoff_button)
        row.addStretch(1)
        self._layout.addWidget(self.buttons)
        self.buttons.setVisible(False)
        self.handoff: Handoff | None = None

    def _toggle_detail(self, checked: bool) -> None:
        self.detail_label.setVisible(checked)
        self.toggle.setText("Hide output" if checked else "Show output")

    def set_outcome(self, outcome: SkillOutcome) -> None:
        result = outcome.result
        color = self.theme.accent if result.ok else self.theme.error
        status = "done" if result.ok else "failed"
        if outcome.decision is not None and not outcome.decision.approved:
            status = "rejected"
            color = self.theme.warning
        summary = html.escape(_short_args(outcome.args or outcome.call.arguments))
        self.text_label.setText(
            f'<span style="color:{color}; font-weight:bold">{status}</span> <code>{summary}</code>'
        )
        full = result.data.get("full_content", result.content)
        self.detail_label.setText(render_plain(full, self.theme))
        self.buttons.setVisible(True)
        if not result.ok:
            self.toggle.setChecked(True)
        self.handoff = result.handoff
        if result.handoff is not None and result.handoff.kind != "done":
            self.handoff_button.setText(result.handoff.label)
            self.handoff_button.setVisible(True)
            self.handoff_button.clicked.connect(lambda: self.on_handoff(result.handoff))
        elif result.handoff is not None:
            self.handoff_button.setText(result.handoff.label)
            self.handoff_button.setEnabled(False)
            self.handoff_button.setVisible(True)


class NoticeBubble(QLabel):
    def __init__(self, text: str, error: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("statusError" if error else "status")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


def _short_args(args: dict, limit: int = 160) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."
