# AI Harness v2

A personal desktop harness (Python + Qt) through which a local LLM acts as an agent on
this machine, and through which you can pick up and continue any work the model starts.
The requirements are in [docs/requirements.md](docs/requirements.md); the design in
[docs/architecture.md](docs/architecture.md); how to add a skill in [docs/skills.md](docs/skills.md).

## Status

Milestones 1 and 2 are implemented, plus most of milestone 3's skills:

- Layout: viewer dock (far left, magnifying), embedded side panel (terminal / image viewer / task
  list), central chat with Markdown and syntax highlighting, the orbiting file explorer (UI3, with
  `ui.file_explorer: tree` as a plain fallback) and sessions panel with full-text search.
- Model bring-up: streaming, native or text tool calling, stop, automatic and manual compaction,
  offline status without losing sessions.
- All twenty skills from the requirements, each with its handoff, plus `generate_image` and
  `generate_3d_model` (with model swapping on the LLM machine), `kicad_inspect` (schematic
  hierarchy, BOM, board, nets straight from KiCad files), `directory_tree` (a tree a few levels
  deep with big folders summarized) and `harness_settings` (ask the model to change the sprite).
- Security: command approval with summarizer, protected paths, private-network blocking, web reader
  in front of all web content, untrusted locations, raw URL gate.
- Logging (three rolling channels), single YAML config with commented defaults, SQLite sessions.

Not yet done: memory across sessions (SH3, deliberately later) and further polish once the explorer and
dock have been used for a while.

## Install

Python 3.11+.

```bash
pip install -e ".[dev]"      # from a clone; the dev extra adds pytest, pytest-qt, ruff
harness                      # launches the GUI
```

On Windows, `pywinpty` is installed automatically for the embedded PowerShell terminal.
VS Code (`code` on PATH) is the default editor for handoffs; change `handoff.editor` in the config.

First launch writes a commented default config to `~/.harness/config.yml` and creates
`~/.harness/logs/`, `~/.harness/skills/` (drop-in skills) and `~/.harness/downloads/`
(an untrusted location). Set `HARNESS_HOME` to use a different folder.

## Model servers

The defaults point every role at a llama.cpp router on the LLM machine (`10.0.0.228:18080`), which
loads models on demand from a presets file, at most two at once: `gpt-oss-120b` for the main model
and `qwen3-coder-30b` for both the summarizer and the web reader. Any OpenAI-compatible llama.cpp
server works too, one per role if you like:

```bash
llama-server -m main-model.gguf --host 0.0.0.0 --port 8080 --jinja -c 32768 --api-key-file key
```

Point `models.main`, `models.summarizer` and `models.web_reader` in the config at them. Each role
takes a list of endpoints tried in order, so the summarizer can run on this machine first and fall
back to the LLM machine (M2), and one server can fill several roles while you experiment (M5).

The server's API key goes in its own file, not in the config: put it on the first line of
`~/.harness/secrets/llama-api-key` (the default `api_key_file` of every endpoint). The model skills
may not read `~/.harness/secrets`, and `harness --check-config` reports a missing or empty key file.

### Generation skills and model swapping

`generate_image` (FLUX.1-schnell) and `generate_3d_model` (Hunyuan3D-2mini, from an image or a
prompt) run in a generation service on the LLM machine; see
[servers/wally_gen/README.md](servers/wally_gen/README.md) for installing it. The LLMs already use
most of that machine's memory, so `models.stack` in the config lists everything that can be
resident, with its measured size and a budget. Before a generation skill runs, the harness unloads
just enough to fit it, lowest `priority` first (the summarizer before the main model; the whole
stack if the job is big enough, or if the component is marked `exclusive`), loads it, runs the job,
and reloads what it unloaded before the main model sees the result. The status line shows each
step. `harness --stack-check` prints what is loaded now.

## Checking the stack

```bash
harness --check-config       # validates the config, naming any bad setting
harness --stack-check        # are the servers and SearXNG reachable?
pytest tests/stack -v -s     # the full stack tests: streaming, tool calling, context size,
                             # summarizer, web reader injection resistance, SearXNG JSON API
```

## Icon and desktop entry

The window and tray icon is the lizard. Windows uses it in the taskbar directly. On Linux, run
`harness --install-desktop-entry` once to write a `.desktop` file and icon under `~/.local/share`
so launchers and the taskbar show the lizard instead of a generic icon.

## The critter

The sprite above the message box animates while the model works. Pick another one under
View > Critter (lizard, turtle, sloth, dinosaur, songbird), or just ask the model; the choice is
saved to `ui.critter` in the config.

## Projects and context

Two places to tell the model things it should always know:

- **Global context**: `~/.harness/context.md`, edited from Session > Global context (or any editor).
  Standing facts for every session: where your papers are, which folder is for scratch work, tools
  you prefer.
- **Projects**: sessions are grouped by project in the sessions panel. A project has a name, an
  optional root directory (new sessions start there) and instructions that go into the model's
  context for every session in it. Create one with the "..." button next to New, or Session > New
  project; double-click a project header to edit it; right-click a session to move it.

Both are injected into the system prompt, so a change applies to the next message.

The sessions panel groups sessions under collapsible project headers. Search covers every session;
**Advanced** adds filters by project, speaker and age, plus regex and case-sensitive matching.

## Resetting

Sessions live in `~/.harness/sessions.sqlite3`. To wipe the chat history and start fresh, use
File > Delete all sessions in the app, or from a shell:

```bash
harness --reset-sessions     # asks for confirmation; config, logs and skills are untouched
```

Deleting the whole `~/.harness/` folder is the full factory reset (the next launch rewrites the
default config; keep a copy of `config.yml` and `secrets/` if you want them back).

## Tests

```bash
pytest                       # unit tests with a fake model; no servers, no screen needed
ruff check src tests && ruff format --check src tests
```

The unit tests cover the config, logging, model client (against a mock server), security policies,
web fetching, the skill framework and every built-in skill, the terminal capture (against a real
bash), the session store and the agent loop, and the Qt widgets offscreen.

## Untested

This code was written without a display or the model stack, so the following need a real run:

- The feel of the orbiting explorer (roll speed, snap, the expand animation) and the dock's
  magnification; the constants at the top of `ui/orbit_explorer.py` and `ui/dock.py` tune them.
- The xterm.js terminal inside QtWebEngine (the Python side and the marker capture are tested; the
  JavaScript page and QWebChannel wiring are not).
- Windows: PowerShell hooks in `src/harness/terminal/shell/harness.ps1`, pywinpty, `explorer`
  handoffs.
- Tool calling and streaming quirks of the actual llama.cpp build and models.
- Desktop notifications and the system tray on the user's desktop environment.
