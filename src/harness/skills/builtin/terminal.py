"""S1: run a command in the shared embedded terminal. Always needs approval (SEC1)."""

from __future__ import annotations

from harness.skills.base import (
    ApprovalRequest,
    Handoff,
    Skill,
    SkillContext,
    SkillError,
    SkillResult,
)
from harness.terminal.session import TerminalBusy, shell_kind_for


class TerminalSkill(Skill):
    name = "terminal"
    description = (
        "Run a shell command in the shared embedded terminal (bash on Linux, PowerShell on Windows) "
        "and get its output and exit code. The user sees the same terminal and inherits its state. "
        "Only use this when no dedicated skill does the job. Set command to an empty string to just "
        "open the terminal for the user."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The exact command line to run. Empty string opens the terminal without running anything.",
            },
            "cwd": {
                "type": "string",
                "description": "Directory to cd into first. Defaults to the session's working directory the first time the terminal opens.",
            },
            "session": {
                "type": "string",
                "description": "Named terminal session; default 'main'. Use another name to keep long tasks separate.",
            },
        },
        "required": ["command"],
    }
    needs_approval = True
    handoff_description = "The embedded terminal, at the relevant working directory, with the command's history and environment."

    def approval_request(self, args, ctx):
        command = args.get("command", "").strip()
        if not command:
            return None  # opening the terminal is harmless
        cwd = args.get("cwd")
        detail = command if not cwd else _in_dir(ctx.resolve(cwd), command, _shell_kind(ctx))
        return ApprovalRequest(
            self.name,
            "Run this command in the terminal?",
            detail,
            "command",
            editable_field="command",
            args=args,
        )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        terminals = ctx.need("terminals")
        session_name = args.get("session") or "main"
        cwd = ctx.resolve(args["cwd"]) if args.get("cwd") else ctx.cwd
        if not cwd.is_dir():
            raise SkillError(f"{cwd} is not a directory")
        session = terminals.get_or_create(session_name, str(ctx.cwd))
        handoff = Handoff.terminal(cwd, session_name)
        command = args["command"].strip()
        if not command:
            if args.get("cwd"):
                _cd(session, cwd, ctx)
            return SkillResult(f"Opened terminal session {session_name!r} in {cwd}.", handoff)
        if args.get("cwd"):
            command = _in_dir(cwd, command, session.shell_kind)
        timeout = ctx.config.skills.timeout_for(self.name)
        try:
            result = session.run_command(command, timeout=timeout, cancel=ctx.cancel)
        except TerminalBusy as exc:
            raise SkillError(f"terminal {session_name!r}: {exc}") from exc
        output = result.output or "(no output)"
        if result.timed_out:
            return SkillResult.fail(
                f"command timed out after {timeout:g}s and was interrupted. Output so far:\n{output}",
                handoff,
            )
        if result.cancelled:
            return SkillResult.fail(
                f"command was cancelled by the user. Output so far:\n{output}", handoff
            )
        if result.paged:
            output += (
                "\n(The command opened a pager, which the harness closed; the output above may "
                "be only the first screen. Pipe through `cat` or use a no-pager option, e.g. "
                "`git --no-pager ...`.)"
            )
        return SkillResult(
            f"exit code: {result.exit_code}\n{output}",
            handoff,
            ok=result.exit_code == 0,
            data={"exit_code": result.exit_code},
        )


def _shell_kind(ctx: SkillContext) -> str:
    terminals = ctx.services.terminals
    return shell_kind_for(terminals.shell_path) if terminals is not None else "bash"


def _quote(path, shell_kind: str = "bash") -> str:
    text = str(path)
    if shell_kind == "powershell":
        return "'" + text.replace("'", "''") + "'"
    if all(ch.isalnum() or ch in "/_-.~:\\" for ch in text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def _cd_command(path, shell_kind: str) -> str:
    if shell_kind == "powershell":
        return f"Set-Location -LiteralPath {_quote(path, shell_kind)}"
    return f"cd {_quote(path)}"


def _in_dir(path, command: str, shell_kind: str) -> str:
    """``command`` runs only if changing to ``path`` worked (Windows PowerShell 5.1 has no &&)."""
    if shell_kind == "powershell":
        return f"{_cd_command(path, shell_kind)}; if ($?) {{ {command} }}"
    return f"{_cd_command(path, shell_kind)} && {command}"


def _cd(session, cwd, ctx) -> None:
    try:
        session.run_command(_cd_command(cwd, session.shell_kind), timeout=10, cancel=ctx.cancel)
    except TerminalBusy:
        pass
