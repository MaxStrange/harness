"""The chat surface: a scrolling column of bubbles, streaming, approvals and notices."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from harness.config import UiConfig
from harness.model.types import Message
from harness.skills.base import ApprovalDecision, Handoff
from harness.skills.runner import PendingApproval, SkillOutcome
from harness.ui.chat.approval_widget import ApprovalWidget
from harness.ui.chat.message_widget import AssistantBubble, NoticeBubble, SkillBubble, UserBubble
from harness.ui.markdown import MarkdownRenderer

RENDER_INTERVAL_MS = 60


class ChatView(QScrollArea):
    def __init__(
        self,
        ui_config: UiConfig,
        on_handoff: Callable[[Handoff], None],
        resolve_approval: Callable[[str, ApprovalDecision], bool],
    ) -> None:
        super().__init__()
        self.theme = ui_config.theme
        self.renderer = MarkdownRenderer(ui_config.theme, ui_config.font_size)
        self.on_handoff = on_handoff
        self.resolve_approval = resolve_approval
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._container)
        self._current: AssistantBubble | None = None
        self._skills: dict[str, SkillBubble] = {}
        self._approvals: list[ApprovalWidget] = []
        self._timer = QTimer(self)
        self._timer.setInterval(RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._flush)
        self._stick_to_bottom = True
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)

    # -- helpers -------------------------------------------------------------

    def _add(self, widget: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def _on_range_changed(self, _min: int, maximum: int) -> None:
        if self._stick_to_bottom:
            self.verticalScrollBar().setValue(maximum)

    def _on_scrolled(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 4

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._current = None
        self._skills.clear()
        self._approvals.clear()
        self._stick_to_bottom = True

    # -- transcript --------------------------------------------------------------

    def load_transcript(
        self, messages: list[Message], skill_events: dict[int, dict] | None = None
    ) -> None:
        """Rebuild the view from stored messages (SH1)."""
        self.clear()
        skill_events = skill_events or {}
        for seq, msg in enumerate(messages):
            if msg.role == "system":
                continue
            if msg.role == "user":
                if msg.content.startswith("[Summary"):
                    self.add_notice("Earlier turns were compacted into a summary.")
                elif msg.content.startswith("[Result of "):
                    continue  # text-mode tool result; the skill bubble covers it
                else:
                    self._add(UserBubble(msg.content, self.theme))
            elif msg.role == "assistant":
                if msg.content.strip():
                    bubble = AssistantBubble(self.renderer)
                    bubble.set_text(msg.content)
                    self._add(bubble)
                for call in msg.tool_calls:
                    self._add(
                        SkillBubble(call.id, call.name, call.arguments, self.theme, self.on_handoff)
                    )
            elif msg.role == "tool":
                self._add_stored_result(msg, skill_events.get(seq))
        self._stick_to_bottom = True

    def _add_stored_result(self, msg: Message, event: dict | None) -> None:
        from harness.model.types import ToolCall
        from harness.skills.base import SkillResult

        ok = bool(event["ok"]) if event else not msg.content.startswith("Error:")
        handoff = Handoff.from_dict(event["handoff"]) if event and event.get("handoff") else None
        bubble = SkillBubble(
            msg.tool_call_id or "", msg.name or "skill", {}, self.theme, self.on_handoff
        )
        bubble.role_label.setText(f"Skill result: {msg.name or ''}")
        outcome = SkillOutcome(
            ToolCall(msg.tool_call_id or "", msg.name or ""),
            SkillResult(msg.content, handoff, ok=ok),
            None,
        )
        bubble.set_outcome(outcome)
        self._add(bubble)

    # -- live events (connected to AgentSignals) ---------------------------------

    def add_user_message(self, text: str) -> None:
        self._add(UserBubble(text, self.theme))
        self._stick_to_bottom = True

    def add_notice(self, text: str, error: bool = False) -> None:
        self._add(NoticeBubble(text, error))

    def on_turn_started(self) -> None:
        self._current = None
        self._timer.start()

    def on_text_delta(self, text: str) -> None:
        if self._current is None:
            self._current = AssistantBubble(self.renderer)
            self._add(self._current)
        self._current.append(text)

    def _flush(self) -> None:
        if self._current is not None:
            self._current.render()

    def on_assistant_message(self, message: Message) -> None:
        if self._current is not None:
            self._current.set_text(message.content)
            if not message.content.strip():
                self._current.setVisible(False)
        elif message.content.strip():
            bubble = AssistantBubble(self.renderer)
            bubble.set_text(message.content)
            self._add(bubble)
        self._current = None

    def on_skill_started(self, call_id: str, skill: str, args: dict) -> None:
        bubble = SkillBubble(call_id, skill, args, self.theme, self.on_handoff)
        self._skills[call_id] = bubble
        self._add(bubble)

    def on_approval_needed(self, pending: PendingApproval) -> None:
        widget = ApprovalWidget(pending, self.theme, self.resolve_approval)
        self._approvals.append(widget)
        self._add(widget)
        self._stick_to_bottom = True

    def on_skill_finished(self, outcome: SkillOutcome) -> None:
        bubble = self._skills.pop(outcome.call.id, None)
        if bubble is None:
            bubble = SkillBubble(
                outcome.call.id,
                outcome.call.name,
                outcome.call.arguments,
                self.theme,
                self.on_handoff,
            )
            self._add(bubble)
        bubble.set_outcome(outcome)

    def on_turn_finished(self, cancelled: bool) -> None:
        self._timer.stop()
        self._flush()
        for widget in self._approvals:
            if not widget.decided:
                widget.cancel("Cancelled" if cancelled else "No longer pending")
        self._approvals.clear()
        if cancelled:
            self.add_notice("Stopped by user.")
        self._current = None
