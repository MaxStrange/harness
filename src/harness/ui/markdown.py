"""Markdown to the HTML subset Qt's rich text engine renders (P15), with Pygments highlighting."""

from __future__ import annotations

import html

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from harness.config import ThemeConfig


class MarkdownRenderer:
    def __init__(self, theme: ThemeConfig, font_size: int = 11) -> None:
        self.theme = theme
        self.font_size = font_size
        self._md = (
            MarkdownIt("commonmark", {"html": False, "linkify": False})
            .enable("table")
            .enable("strikethrough")
        )
        theme = self.theme
        font_size = self.font_size
        formatter = HtmlFormatter(noclasses=True, style="monokai", nowrap=True)

        # markdown-it binds rule functions to its own renderer, so these are closures, not methods.
        def render_fence(_renderer, tokens, idx, options, env):
            token = tokens[idx]
            lang = (token.info or "").strip().split()[0] if token.info else ""
            try:
                lexer = get_lexer_by_name(lang) if lang else TextLexer()
            except ClassNotFound:
                lexer = TextLexer()
            body = highlight(token.content, lexer, formatter).rstrip("\n")
            header = (
                f'<span style="color:{theme.text_muted}; font-size:{max(7, font_size - 2)}pt">{html.escape(lang)}</span><br/>'
                if lang
                else ""
            )
            return (
                f'<table width="100%" cellpadding="6" cellspacing="0" style="background:{theme.code_background}; border-radius:6px; margin:4px 0"><tr><td>'
                f'{header}<pre style="font-family:monospace; color:{theme.text}; margin:0; white-space:pre-wrap">{body}</pre></td></tr></table>'
            )

        def render_code_inline(_renderer, tokens, idx, options, env):
            return f'<code style="background:{theme.code_background}; color:{theme.accent}; font-family:monospace">{html.escape(tokens[idx].content)}</code>'

        self._md.add_render_rule("fence", render_fence)
        self._md.add_render_rule("code_block", render_fence)
        self._md.add_render_rule("code_inline", render_code_inline)

    def render(self, text: str) -> str:
        body = self._md.render(text)
        return f'<div style="color:{self.theme.text}">{body}</div>'


def render_plain(text: str, theme: ThemeConfig) -> str:
    """Preformatted text (tool output) with escaping only."""
    return f'<pre style="font-family:monospace; color:{theme.text}; white-space:pre-wrap; margin:0">{html.escape(text)}</pre>'


def render_diff(diff: str, theme: ThemeConfig) -> str:
    lines = []
    for line in diff.splitlines():
        escaped = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(f'<span style="color:{theme.accent}">{escaped}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            lines.append(f'<span style="color:{theme.error}">{escaped}</span>')
        elif line.startswith("@@"):
            lines.append(f'<span style="color:{theme.warning}">{escaped}</span>')
        else:
            lines.append(escaped)
    return f'<pre style="font-family:monospace; white-space:pre-wrap; margin:0">{"<br/>".join(lines)}</pre>'
