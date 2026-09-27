"""S17: start a long-running command and check on it later."""

from __future__ import annotations

from harness.skills.base import (
    ApprovalRequest,
    Handoff,
    Skill,
    SkillContext,
    SkillError,
    SkillResult,
)


class BackgroundCommandSkill(Skill):
    name = "background_command"
    description = (
        "Start a long-running command in its own terminal session and return immediately with a job id; "
        "later call with action 'check' to read new output and status, or 'stop' to interrupt it. "
        "action 'list' shows all jobs."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["start", "check", "stop", "list"]},
            "command": {"type": "string", "description": "For start: the command to run."},
            "job_id": {
                "type": "string",
                "description": "For check/stop: the job id returned by start.",
            },
            "cwd": {
                "type": "string",
                "description": "For start: directory to run in (default: session working directory).",
            },
        },
        "required": ["action"],
    }
    needs_approval = True
    handoff_description = "The embedded terminal attached to the running process."

    def approval_request(self, args, ctx):
        if args.get("action") != "start":
            return None
        return ApprovalRequest(
            self.name,
            "Start this command in the background?",
            args.get("command", ""),
            "command",
            editable_field="command",
            args=args,
        )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        jobs = ctx.need("background_jobs")
        action = args["action"]
        if action == "start":
            command = (args.get("command") or "").strip()
            if not command:
                raise SkillError("command is required to start a job")
            cwd = ctx.resolve(args["cwd"]) if args.get("cwd") else ctx.cwd
            job = jobs.start(command, str(cwd))
            return SkillResult(
                f"Started job {job.id} in terminal session {job.session_name!r}. Check it with action 'check'.",
                Handoff.terminal(cwd, job.session_name, label="Attach to job"),
                data={"job_id": job.id},
            )
        if action == "list":
            lines = [
                f"{j.id}: {'running' if j.running else 'finished'} - {j.command}"
                for j in jobs.all()
            ] or ["(no jobs)"]
            return SkillResult("\n".join(lines), Handoff.terminal(ctx.cwd, label="Open terminal"))
        job = jobs.get(args.get("job_id") or "")
        if job is None:
            raise SkillError(f"unknown job id {args.get('job_id')!r}")
        handoff = Handoff.terminal(job.cwd, job.session_name, label="Attach to job")
        if action == "stop":
            jobs.stop(job)
            return SkillResult(f"Sent interrupt to job {job.id}.", handoff)
        output = jobs.new_output(job)
        if job.error:
            status = f"failed to run: {job.error}"
        elif job.result is not None:
            status = f"finished with exit code {job.result.exit_code}" + (
                " (timed out)" if job.result.timed_out else ""
            )
        else:
            status = "still running"
        return SkillResult(
            f"Job {job.id} is {status}.\nNew output:\n{output or '(none)'}",
            handoff,
            data={"running": job.running},
        )
