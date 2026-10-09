# Architecture

Everything lives in `src/harness/`. Nothing outside `ui/` imports Qt, so the core can be tested
headless and reused by the CLI and the stack tests.

```
config.py         schema (pydantic) + loader + commented default_config.yml (CFG1, CFG2, P16)
logging_setup.py  three rolling channels: internal, prompts, audit (LOG1-3)
paths.py          ~/.harness layout (HARNESS_HOME overrides)
bootstrap.py      build_core(): wires models, policies, skills, store, terminals

model/            types, OpenAI-compatible streaming client (native + text tool calls), role
                  clients with endpoint failover, summarizer, web reader, compaction, fake model,
                  stack.py: what is resident on the model machine and swapping it for skills
security/         path policy (deny list, untrusted dirs), net policy (no private addresses)
web/              safe fetcher (checked redirects, no cookies, size cap), HTML->text, SearXNG
skills/           the contract (base.py), discovery (registry.py), execution (runner.py),
                  builtin/ one file per skill, tasklist_store.py
terminal/         markers (OSC 7331 capture), pty backends, TerminalSession, manager + jobs,
                  shell/ init scripts, web/ xterm.js page
agent/            SQLite store with FTS5, system prompt, the agent loop
ui/               Qt: theme, markdown, handoff execution, thread bridges, widgets, main window;
                  orbit_explorer.py is UI3 (rings of icons seen edge-on, a mouse lens
                  with depth-of-field blur after Stéphane Bourez's Screenvader, mouse roll at the sides), file_explorer.py the tree fallback
cli.py            the `harness` command
```

## Threads

- The GUI thread owns every widget. It never blocks: model calls and skills run elsewhere (P13).
- `AgentController.send()` runs `Agent.send()` on a thread. The agent reports through the
  `AgentEvents` protocol; `ui/bridge.py` turns those into Qt signals (queued to the GUI thread).
- Skills run in the runner's thread pool with a timeout. They reach the GUI only through
  `Services.ui` (`QtUiBridge`), whose calls hop to the GUI thread via signals;
  `open_external` waits for the result through `MainThreadInvoker`.
- Approvals: the runner calls `ApprovalBroker.request()`, which blocks the skill thread on an
  event; the UI shows the card and calls `broker.resolve()`. The summarizer runs on its own thread
  so the card appears immediately and the summary fills in when it is ready (SEC1, SEC1a).
- Each terminal has a pty reader thread; output goes to subscribers (the xterm view, through a
  queued signal) and through the marker parser for command capture.
- Cancellation is one `CancelToken` per turn, checked by the model stream, the approval wait,
  cooperative skills and the terminal command wait (P3).

## A turn

1. The user message is appended and persisted; compaction runs first if the estimate exceeds
   `context_window * compaction_threshold` (P4).
2. The main model streams; text deltas go to the chat, the final message may carry tool calls.
3. For each call the runner validates arguments against the skill's schema, asks for approval if
   needed, runs the skill with its timeout, truncates the result for the model (the full text stays
   in the audit log and the UI), and the result is appended as a `tool` message.
4. Back to 2 until the model answers without tool calls (or `MAX_TOOL_ROUNDS`).

A generation skill calls `ModelStack.acquire([...])`, which may unload router models (even the
main one) to make room. Before step 2 runs again, and at the end of every turn, the agent calls
`ModelStack.restore()`, which reloads what was unloaded, making room only by unloading what the
skill loaded. So the main model always gets the skill's result with the stack it started with.
Planning (`stack.plan`) is a pure function over the declared sizes in `models.stack`.

## Terminal capture (P10, P11a)

bash starts with `--rcfile shell/harness.bashrc`, which sources the user's `~/.bashrc` and sets
`PS0` (emits `ESC ] 7331 ; S BEL` after a command is read) and `PROMPT_COMMAND` (emits
`ESC ] 7331 ; E ; <exit code> BEL` before each prompt). PowerShell gets the same from
`PSConsoleHostReadLine` and `prompt` in `shell/harness.ps1`. `TerminalSession.run_command` types
the command, waits for S, captures until E, and returns the text and exit code. The user and the
model share the shell, so `cd`, exports and history carry over. Multi-line commands are wrapped in
`eval "$(cat <<'__HARNESS_EOF__' ... )"` so they are read as one command. The markers are stripped
before the bytes reach xterm.js.

## Security model

- Every raw terminal command is approved by the user, shown verbatim with a summary (SEC1).
- Only the web reader model reads web content, search results and files under untrusted
  locations; the main model sees its report (SEC2, SEC5). `get_url_raw` is the single gated
  exception (SEC2a).
- Web traffic: SearXNG only, direct HTTP, no cookies, no hosted services (SEC3); no private
  addresses except hosts listed one by one, the SearXNG host implied (SEC4); redirects are
  re-checked hop by hop.
- Protected paths (`security.deny_paths`, `deny_names`) require approval for read-only skills and
  are flagged on writes (P9).

## Extending

- New skill: one file, see `docs/skills.md`.
- New embedded view: `EMBEDDED_VIEWS` + a widget in `ui/viewers/` + `MainWindow.show_embedded`.
- New config setting: add it to the pydantic model in `config.py` and to `default_config.yml`
  (a unit test checks the two agree).
- Text tool format for models without native function calling: `models.main.tool_format: text`.

## Next steps

- STEP files in the 3D viewer (occt-import-js, OpenCascade as WebAssembly, would read them in
  the page), textured meshes (Hunyuan's texture stage needs a CUDA
  rasterizer ported to ROCm), and animation generation.
- The stack serialises swaps within one harness, but a second session calling the main model
  while a skill has it unloaded makes the router load it again; a lease across sessions would
  close that.

- Keyboard navigation across panels, better skill-result rendering (tables, images).
- Session export, memory across sessions (SH3, later).
