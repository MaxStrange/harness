"""S9: read-only git."""

from __future__ import annotations

import shutil
import subprocess

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult

ALLOWED = {
    "status": ["status", "--short", "--branch"],
    "log": ["log", "--oneline", "--decorate", "--graph", "-n"],
    "diff": ["diff"],
    "diff_staged": ["diff", "--cached"],
    "show": ["show", "--stat", "--patch"],
    "branches": ["branch", "-a", "-vv"],
    "remotes": ["remote", "-v"],
    "blame": ["blame"],
}


class GitInspectSkill(Skill):
    name = "git_inspect"
    description = (
        "Read-only git inspection of a repository: status, log, diff (working tree), diff_staged, "
        "show (a commit), branches, remotes, blame (a file). Never modifies the repository; "
        "use the terminal skill for commits, checkouts and pushes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(ALLOWED)},
            "path": {
                "type": "string",
                "description": "Repository (or a path inside it). Default: session working directory.",
            },
            "target": {
                "type": "string",
                "description": "For show: a commit; for blame/diff: a file path relative to the repo; for log: a ref.",
            },
            "count": {"type": "integer", "description": "For log: number of commits (default 20)."},
        },
        "required": ["action"],
    }
    handoff_description = "The embedded terminal opens in the repository."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        git = shutil.which("git")
        if git is None:
            raise SkillError("git is not installed or not on PATH")
        repo = ctx.resolve(args.get("path") or ".")
        if not repo.exists():
            raise SkillError(f"{repo} does not exist")
        if repo.is_file():
            repo = repo.parent
        action = args["action"]
        argv = [git, "-c", "color.ui=never", *ALLOWED[action]]
        target = args.get("target")
        if action == "log":
            argv.append(str(int(args.get("count") or 20)))
            if target:
                argv.append(target)
        elif action in ("show", "blame") and target:
            argv.append(target)
        elif action in ("diff", "diff_staged") and target:
            argv.extend(["--", target])
        elif action == "blame" and not target:
            raise SkillError("blame needs a target file")
        try:
            proc = subprocess.run(
                argv,
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=ctx.config.skills.timeout_for(self.name),
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise SkillError(f"git timed out: {exc}") from exc
        handoff = Handoff.terminal(repo, label="Open terminal in repo")
        if proc.returncode != 0:
            return SkillResult.fail(
                f"git exited with {proc.returncode}:\n{proc.stderr.strip() or proc.stdout.strip()}",
                handoff,
            )
        output = proc.stdout.strip() or "(no output)"
        return SkillResult(f"$ git {' '.join(argv[3:])}\n{output}", handoff)
