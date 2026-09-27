"""S16: running processes and open ports."""

from __future__ import annotations

import sys

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult


class ProcessListSkill(Skill):
    name = "process_list"
    description = "List running processes (top by CPU or memory, or matching a name) and listening network ports."
    parameters = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Only processes whose name or command line contains this text.",
            },
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "memory", "pid"],
                "description": "Default cpu.",
            },
            "limit": {"type": "integer", "description": "Default 30."},
            "ports": {"type": "boolean", "description": "Also list listening ports. Default true."},
        },
    }
    handoff_description = (
        "The embedded terminal with the equivalent command (ps / Get-Process, ss / netstat)."
    )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        try:
            import psutil
        except ImportError as exc:  # pragma: no cover
            raise SkillError("psutil is not installed") from exc
        needle = (args.get("filter") or "").lower()
        limit = int(args.get("limit") or 30)
        rows = []
        for proc in psutil.process_iter(
            ["pid", "name", "cmdline", "memory_info", "cpu_percent", "username"]
        ):
            ctx.check_cancelled()
            info = proc.info
            cmdline = " ".join(info.get("cmdline") or [])[:150]
            if (
                needle
                and needle not in (info.get("name") or "").lower()
                and needle not in cmdline.lower()
            ):
                continue
            mem = info.get("memory_info").rss if info.get("memory_info") else 0
            rows.append(
                (
                    info["pid"],
                    info.get("name") or "?",
                    info.get("cpu_percent") or 0.0,
                    mem,
                    info.get("username") or "",
                    cmdline,
                )
            )
        key = {"cpu": lambda r: -r[2], "memory": lambda r: -r[3], "pid": lambda r: r[0]}[
            args.get("sort_by") or "cpu"
        ]
        rows.sort(key=key)
        lines = [f"{'PID':>7} {'CPU%':>5} {'MEM':>8}  NAME  COMMAND"]
        for pid, name, cpu, mem, _user, cmdline in rows[:limit]:
            lines.append(f"{pid:>7} {cpu:>5.1f} {mem / 1048576:>7.0f}M  {name}  {cmdline}")
        lines.append(f"({len(rows)} processes matched)")
        if args.get("ports", True):
            lines.append("\nlistening ports:")
            try:
                conns = [
                    c for c in psutil.net_connections(kind="inet") if c.status == psutil.CONN_LISTEN
                ]
                names = {p.pid: p.name() for p in psutil.process_iter(["name"])} if conns else {}
                for c in sorted(conns, key=lambda c: c.laddr.port):
                    lines.append(
                        f"  {c.laddr.ip}:{c.laddr.port}  pid {c.pid or '?'} {names.get(c.pid, '')}"
                    )
                if not conns:
                    lines.append("  (none visible)")
            except (psutil.AccessDenied, PermissionError) as exc:
                lines.append(f"  (not permitted to list ports: {exc})")
        cmd = "Get-Process; netstat -ano" if sys.platform == "win32" else "ps aux; ss -tlnp"
        return SkillResult(
            "\n".join(lines), Handoff.terminal(ctx.cwd, label=f"Open terminal ({cmd})")
        )
