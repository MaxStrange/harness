"""S15: OS, disk space, installed tools."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.skills.builtin._files import human_size

TOOLS = [
    "git",
    "python3",
    "python",
    "pip",
    "node",
    "npm",
    "code",
    "docker",
    "gcc",
    "make",
    "cmake",
    "rustc",
    "cargo",
    "go",
    "java",
    "ffmpeg",
    "pwsh",
    "powershell",
]


class SystemInfoSkill(Skill):
    name = "system_info"
    description = "Report the OS, CPU, memory, disk space and which common developer tools are installed with their versions."
    parameters = {
        "type": "object",
        "properties": {
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Extra tool names to check for.",
            }
        },
    }
    handoff_description = "The embedded terminal with equivalent commands available."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        lines = [
            f"os: {platform.platform()}",
            f"machine: {platform.machine()} ({platform.processor() or 'unknown cpu'})",
            f"python: {sys.version.split()[0]} at {sys.executable}",
            f"hostname: {platform.node()}",
        ]
        try:
            import psutil

            mem = psutil.virtual_memory()
            lines.append(f"memory: {human_size(mem.available)} free of {human_size(mem.total)}")
            lines.append(
                f"cpu: {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads"
            )
        except Exception:  # noqa: BLE001
            pass
        for mount in _mounts():
            try:
                usage = shutil.disk_usage(mount)
                lines.append(
                    f"disk {mount}: {human_size(usage.free)} free of {human_size(usage.total)}"
                )
            except OSError:
                continue
        lines.append("tools:")
        for tool in TOOLS + list(args.get("tools") or []):
            ctx.check_cancelled()
            path = shutil.which(tool)
            if path is None:
                continue
            lines.append(f"  {tool}: {_version(path)} ({path})")
        cmd = "Get-ComputerInfo" if sys.platform == "win32" else "uname -a; df -h"
        return SkillResult(
            "\n".join(lines), Handoff.terminal(ctx.cwd, label=f"Open terminal ({cmd})")
        )


def _mounts() -> list[str]:
    if sys.platform == "win32":
        import string

        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    mounts = ["/"]
    try:
        import psutil

        for part in psutil.disk_partitions(all=False):
            if part.mountpoint not in mounts and not part.mountpoint.startswith(("/snap", "/boot")):
                mounts.append(part.mountpoint)
    except Exception:  # noqa: BLE001
        pass
    return mounts[:8]


def _version(path: str) -> str:
    for flag in ("--version", "-version", "version"):
        try:
            proc = subprocess.run(
                [path, flag], capture_output=True, text=True, timeout=5, errors="replace"
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (proc.stdout or proc.stderr).strip().splitlines()
        if text and proc.returncode == 0:
            return text[0][:120]
    return "version unknown"
