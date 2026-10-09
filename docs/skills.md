# Writing a skill

A skill is one Python file with one class. Drop it into `~/.harness/skills/` (or
`src/harness/skills/builtin/` for a built-in) and restart the harness; nothing else changes.

```python
from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult


class WordCountSkill(Skill):
    name = "word_count"                       # lowercase, digits, underscores
    description = "Count the words in a text file."      # what the model sees
    parameters = {                            # JSON schema of the arguments
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    needs_approval = False                    # True -> the user must approve every call
    timeout_s = 10                            # optional; config skills.timeouts overrides
    handoff_description = "The file opens in the editor."   # required (F3)

    def approval_request(self, args, ctx):
        # Optional: decide per call. This makes protected paths ask (P9).
        return self.protected_path_request(ctx.resolve(args["path"]), args, ctx)

    def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        path = ctx.resolve(args["path"])      # relative paths resolve against the session cwd
        if not path.is_file():
            raise SkillError(f"{path} is not a file")   # goes to the model and the user
        words = len(path.read_text(errors="replace").split())
        return SkillResult(f"{path} has {words} words.", Handoff.editor(path))
```

## The contract

| Declaration | Meaning |
| --- | --- |
| `name`, `description`, `parameters` | What the model sees. The harness adds a `handoff: boolean` parameter to every skill (H3); do not declare it yourself. |
| `needs_approval` | Static default. Override `approval_request()` to decide per call and to control what the approval card shows (`detail_kind` is `command`, `diff` or `text`; `editable_field` lets the user edit an argument before approving). Commands get a plain-language summary from the summarizer automatically. |
| `timeout_s` | Every call has a timeout (P7). `skills.timeouts.<name>` in the config wins. |
| `handoff_description` | One sentence: how the user takes over. Required. |
| `run(args, ctx)` | Returns a `SkillResult`. Raise `SkillError` for expected failures; anything else is caught, logged with its traceback, and reported too (P7). |

`SkillResult(content, handoff, ok, error, data)`: `content` goes to the model, everything is shown
to the user, and `handoff` is offered as a button (or performed immediately when the model passed
`handoff: true`).

## Handoffs

- `Handoff.editor(path, line)`, `Handoff.file_manager(path)`, `Handoff.browser(url)`,
  `Handoff.default_app(target)`: external, performed with the configured editor / the platform's
  file manager / the default application.
- `Handoff.terminal(cwd, session)`, `Handoff.image(path)`, `Handoff.model(path)` (3D viewer),
  `Handoff.tasks()`: embedded views inside
  the harness. Adding a new embedded view means adding it to `EMBEDDED_VIEWS` in
  `skills/base.py`, a widget under `ui/viewers/`, and a case in `MainWindow.show_embedded`.
- `Handoff.done(label)`: the skill itself was the handoff (clipboard, notification).

## Context and services

`ctx` gives you the session working directory, the config, the cancel token
(`ctx.check_cancelled()` inside loops), the session id, and `ctx.services`:

- `path_policy` (protected paths, untrusted locations), `net_policy`, `fetcher` (safe HTTP),
  `searx`, `web_reader`, `terminals`, `background_jobs`, `task_lists`, and `ui`
  (`notify`, `set_clipboard`, `open_external`, `open_embedded`; safe to call from the skill thread).
- `ctx.need("fetcher")` raises a clear error when a service is missing.

Skills run on worker threads. Never touch Qt widgets directly; go through `ctx.services.ui`.

## Skills that need a model on the LLM machine

Call `ctx.services.stack.acquire(["image"], progress=ctx.say, cancel=ctx.cancel)` (see
`builtin/_generation.py:make_room`) before using a component listed in `models.stack`. Do not
restore afterwards: the agent does that before the main model's next call, so several generation
calls in one round swap only once. `ctx.say(text)` puts a line in the status bar during long steps.

## Untrusted content

Anything from the web is untrusted (SEC2, SEC5). A skill that returns such content to the model
must route it through `ctx.services.web_reader` (see `builtin/_files.py:through_reader_if_untrusted`
and `builtin/web_fetch.py`). Only `get_url_raw` hands raw web text to the model, and only with
approval.

## Testing a skill

```python
from harness.config import Config
from harness.model.types import ToolCall
from harness.skills.base import Services, SkillContext
from harness.skills.registry import SkillRegistry
from harness.skills.runner import SkillRunner, auto_approve_broker

registry = SkillRegistry()
registry.register(WordCountSkill)
runner = SkillRunner(registry, auto_approve_broker())
ctx = SkillContext(cwd=tmp_path, config=Config(), services=Services())
outcome = runner.execute(ToolCall("c1", "word_count", {"path": "README.md"}), ctx)
assert outcome.result.ok
```

`tests/unit/test_builtin_skills.py` has examples for approval flows, untrusted files, fake web
servers and a real terminal.
