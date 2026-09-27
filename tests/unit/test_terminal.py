from __future__ import annotations

import base64
import shutil
import sys

import pytest

from harness.config import TerminalConfig
from harness.terminal.manager import BackgroundJobs, TerminalManager
from harness.terminal.markers import (
    MarkerParser,
    split_segments,
    strip_escapes,
    wrap_for_bash,
    wrap_for_powershell,
)
from harness.terminal.session import TerminalBusy, TerminalSession

bash_only = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash"), reason="needs bash"
)
powershell_only = pytest.mark.skipif(
    sys.platform != "win32" or not shutil.which("powershell.exe"), reason="needs Windows PowerShell"
)


def test_marker_parser_segments_are_ordered():
    p = MarkerParser()
    segs = p.feed(b"a\x1b]7331;S\x07b\x1b]7331;E;0\x07c")
    assert [s if isinstance(s, bytes) else s.kind for s in segs] == [b"a", "S", b"b", "E", b"c"]


def test_marker_parser_strips_and_reports():
    p = MarkerParser()
    clean, events = split_segments(
        p.feed(b"prompt$ \x1b]7331;S\x07hello\r\n\x1b]7331;E;3\x07prompt$ ")
    )
    assert clean == b"prompt$ hello\r\nprompt$ "
    assert [(e.kind, e.exit_code) for e in events] == [("S", None), ("E", 3)]


def test_marker_parser_handles_split_marker_and_st_terminator():
    p = MarkerParser()
    out = b""
    events = []
    for chunk in [b"a\x1b", b"]73", b"31;E;", b"12\x1b", b"\\b", b"\x1b]7331;S\x1b\\c"]:
        c, e = split_segments(p.feed(chunk))
        out += c
        events += e
    out += p.flush()
    assert out == b"abc"
    assert [(e.kind, e.exit_code) for e in events] == [("E", 12), ("S", None)]


def test_marker_parser_leaves_other_escapes_alone():
    p = MarkerParser()
    clean, events = split_segments(p.feed(b"\x1b[31mred\x1b[0m \x1b]0;title\x07 \x1b]7331;X\x07"))
    assert clean == b"\x1b[31mred\x1b[0m \x1b]0;title\x07 \x1b]7331;X\x07"
    assert events == []
    # a lone trailing ESC is held, then released on flush
    clean, _ = split_segments(p.feed(b"x\x1b"))
    assert clean == b"x"
    assert p.flush() == b"\x1b"


def test_wrap_for_bash_multiline_uses_eval_heredoc():
    assert wrap_for_bash("ls -la\n") == "ls -la"
    wrapped = wrap_for_bash("export A=1\necho $A")
    assert wrapped.startswith('eval "$(cat <<') and "export A=1\necho $A" in wrapped


@bash_only
def test_real_bash_session_captures_output_and_exit_code(tmp_path):
    session = TerminalSession("t", "/bin/bash", str(tmp_path))
    received = []
    session.subscribe(received.append)
    session.start()
    try:
        assert session.wait_for_prompt(10)
        result = session.run_command(
            "echo hello; echo err 1>&2; exit_code_test() { return 7; }; exit_code_test", timeout=10
        )
        assert result.exit_code == 7
        assert "hello" in result.output and "err" in result.output
        assert "7331" not in result.output
        # Environment persists between commands (P10): the model's exports are the user's.
        assert session.run_command("export HARNESS_TEST_VAR=42", timeout=10).exit_code == 0
        assert session.run_command("echo $HARNESS_TEST_VAR", timeout=10).output.strip() == "42"
        # Multi-line commands run as one unit.
        multi = session.run_command("for i in 1 2 3; do\n  echo line$i\ndone", timeout=10)
        assert multi.output.splitlines() == ["line1", "line2", "line3"] and multi.exit_code == 0
        # cd persists too, and relative cwd is the tmp dir.
        (tmp_path / "sub").mkdir()
        session.run_command("cd sub", timeout=10)
        assert session.run_command("pwd", timeout=10).output.strip().endswith("sub")
        # Markers never reach the subscribers (the xterm view).
        assert b"7331" not in b"".join(received)
    finally:
        session.close()


@bash_only
def test_real_bash_timeout_interrupts(tmp_path):
    session = TerminalSession("t", "/bin/bash", str(tmp_path))
    session.start()
    try:
        assert session.wait_for_prompt(10)
        result = session.run_command("sleep 30", timeout=0.5)
        assert result.timed_out
        assert session.wait_for_prompt(5)
        assert session.run_command("echo back", timeout=10).output.strip() == "back"
    finally:
        session.close()


@bash_only
def test_busy_terminal_refuses(tmp_path):
    session = TerminalSession("t", "/bin/bash", str(tmp_path))
    session.start()
    try:
        assert session.wait_for_prompt(10)
        session.write("cat\n")  # a program is now running
        import time

        time.sleep(0.3)
        with pytest.raises(TerminalBusy):
            session.run_command("echo x", timeout=5)
        session.interrupt()
    finally:
        session.close()


@bash_only
def test_manager_and_background_jobs(tmp_path):
    manager = TerminalManager(TerminalConfig())
    created = []
    manager.on_created.append(created.append)
    try:
        jobs = BackgroundJobs(manager)
        job = jobs.start("echo start; sleep 0.5; echo finished", str(tmp_path))
        assert manager.get(job.session_name) is not None and created
        job.thread.join(10)
        assert job.result is not None and job.result.exit_code == 0
        assert "finished" in jobs.new_output(job)
        assert jobs.new_output(job) == ""
        main = manager.get_or_create("main", str(tmp_path))
        assert manager.get_or_create("main", str(tmp_path)) is main
    finally:
        manager.close_all()


def test_wrap_for_powershell_multiline_is_one_line():
    assert wrap_for_powershell("Get-Date\n") == "Get-Date"
    body = "$y = @'\nit's\n'@\n$y"
    wrapped = wrap_for_powershell(body)
    assert "\n" not in wrapped and wrapped.startswith("Invoke-Expression ")
    encoded = wrapped.split("'")[1]
    assert base64.b64decode(encoded).decode("utf-8") == body


def test_strip_escapes_removes_colours_titles_and_cursor_moves():
    title = "\x1b]0;C:\\ps.exe\x1b\\"  # OSC window title, ST-terminated, as ConPTY sends it
    text = f"\x1b[?7l\x1b[?7h{title}\x1b[0;91mError\x1b[0m\x1b[16;63H\x1b=done\x1b(B"
    assert strip_escapes(text) == "Errordone"


@powershell_only
def test_real_powershell_session_captures_output_and_exit_code(tmp_path):
    session = TerminalSession("t", "powershell.exe", str(tmp_path))
    received = []
    session.subscribe(received.append)
    session.start()
    try:
        assert session.wait_for_prompt(30)
        assert session.run_command("echo hello", timeout=20).output == "hello"
        # Native exit codes and cmdlet failures.
        assert session.run_command("cmd /c exit 3", timeout=20).exit_code == 3
        failed = session.run_command("Get-Item no_such_item", timeout=20)
        assert failed.exit_code == 1 and "\x1b" not in failed.output
        # Multi-line commands (here-strings, blocks) run as one unit and share scope (P10).
        multi = session.run_command("$y = @'\nit's here\n'@\nif ($true) {\n  $y\n}", timeout=20)
        assert multi.output == "it's here" and multi.exit_code == 0
        assert session.run_command("$y.Length", timeout=20).output == "9"
        (tmp_path / "sub").mkdir()
        session.run_command("cd sub", timeout=20)
        assert session.run_command("(Get-Location).Path", timeout=20).output.endswith("sub")
        assert session.run_command("Write-Output 'h\u00e9llo \u2713'", timeout=20).output == (
            "h\u00e9llo \u2713"
        )
        assert b"7331" not in b"".join(received)
    finally:
        session.close()


@powershell_only
def test_real_powershell_timeout_interrupts(tmp_path):
    session = TerminalSession("t", "powershell.exe", str(tmp_path))
    session.start()
    try:
        assert session.wait_for_prompt(30)
        result = session.run_command("Start-Sleep 30", timeout=1)
        assert result.timed_out
        assert session.wait_for_prompt(10)
        assert session.run_command("echo back", timeout=20).output == "back"
    finally:
        session.close()


def test_terminal_skill_changes_directory_per_shell():
    from harness.skills.builtin.terminal import _in_dir

    assert _in_dir("/tmp/a b", "ls", "bash") == "cd '/tmp/a b' && ls"
    # Windows PowerShell 5.1 has no &&, and doubles single quotes.
    assert _in_dir(r"C:\it's", "dir", "powershell") == (
        r"Set-Location -LiteralPath 'C:\it''s'; if ($?) { dir }"
    )
