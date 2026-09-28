# AI Harness v2 — Requirements

Sep 25, 2026

## 1. Purpose and background

This document defines the requirements for version 2 of a personal AI harness: a desktop application through which an AI model can act as an agent on the local machine, and through which the user can pick up and continue any work the model starts.

Version 1 was built on Pi running inside the WezTerm terminal. It was a useful prototype, but it kept fighting its own design foundations. The main lesson was that a terminal-first interface is the wrong base: the work this harness does needs a GUI. Version 2 is a ground-up rebuild on a GUI foundation rather than an iteration on v1.

Nothing from v1 is carried over. Version 2 starts fresh.

## 2. Hard constraints

**C1. Python and Qt.** The application must be written in Python using Qt for the GUI. This is a requirement, not an implementation detail: the owner knows Python and Qt, and the harness must be maintainable by one person with minimal effort.

**C2. Maintainability first.** Where a design choice trades cleverness or feature breadth against ease of maintenance, maintenance wins. New capabilities should be addable without restructuring existing code.

**C3. Platforms.** The application must run on Linux and Windows. On Windows, the embedded terminal uses PowerShell.

## 3. Tool calling and skill framework

The harness exposes a tool-calling interface to the model. Capabilities the model can use are packaged as **skills**; skills are how the model acts as an agent on the local machine.

**F1. Extensible by design.** The framework must be ready for new skills from day one. Adding a skill should mean adding a self-contained unit (its definition, the description the model sees, its implementation, and its handoff behavior) without modifying the core harness.

**F2. Uniform contract.** Every skill follows the same contract so the harness can register it, present it to the model, run it, display its result, and offer handoff in a consistent way.

**F3. Handoff is part of the contract.** Every skill must define how its result is handed to the user for manual follow-up (see section 5). A skill without a handoff path is incomplete.

**F4. Prefer dedicated skills.** The harness injects instructions telling the model to prefer dedicated skills over raw terminal commands whenever a dedicated skill can do the job.

## 4. Initial skills

The first release must include at least these skills. The list is a floor, not a ceiling.

| Skill | What the model can do | Handoff to user |
| --- | --- | --- |
| **S1. Terminal** | Open a terminal and run commands | Embedded terminal window in the app, at the relevant working directory |
| **S2. Web** | Pull information from the web | URL that opens in the user's default browser |
| **S3. PDF** | Read PDF files | The PDF opens for the user to view in the system's default PDF application (on most systems, the web browser) |

Example interaction for S1: the user says "open the terminal for me in the repos directory" and the harness opens an embedded terminal already in that directory, ready for the user to type into.

### Read-only file skills

Common harmless operations get their own skills instead of going through the raw terminal, so they can run without approval (see SEC1).

| Skill | What the model can do | Handoff to user |
| --- | --- | --- |
| **S4. Read file** | Read the contents of a file | File opens in VS Code |
| **S5. List directory** | List the contents of a directory | Directory opens in the system file manager |
| **S6. Search for files** | Find files by name or pattern | Any result opens in VS Code or the file manager |
| **S7. Search file contents** | Grep for text within files | Any match opens in VS Code at that line |
| **S8. File info** | Get size, dates, and type of a file | File opens in the file manager |
| **S9. Git inspect** | Read-only git: status, log, diff | Embedded terminal opens in the repo |
| **S10. View image** | Open an image file | Embedded image viewer |

### Additional skills

Skills that make the agent more capable and reduce how often it falls back to raw terminal commands.

| Skill | What the model can do | Needs approval | Handoff to user |
| --- | --- | --- | --- |
| **S11. Write / edit file** | Create a file or make targeted edits to one | Yes, shown as a diff | Changed file opens in VS Code |
| **S12. Web search** | Search the web, split out from S2 so S2 becomes fetching a specific page | No (results go through the web reader) | Results open in the browser |
| **S13. Open with default app** | Open any file or URL in its default application | No | This is the handoff itself |
| **S14. Copy to clipboard** | Put text on the clipboard | No | User pastes it wherever needed |
| **S15. System info** | OS, disk space, installed tools and their versions | No | Embedded terminal with the equivalent command |
| **S16. Process list** | List running processes and open ports | No | Embedded terminal with the equivalent command |
| **S17. Background command** | Start a long-running command and check its output later | Yes (same as S1) | Embedded terminal attached to the running process |
| **S18. Task list** | Keep a visible checklist while working through multi-step tasks | No | Checklist panel in the app (a new embedded view) |
| **S19. Notify** | Send a desktop notification, e.g. when a long task finishes | No | Clicking it brings up the session |
| S20. Get URL (raw) | Fetch a URL and give its raw content directly to the main model | Yes, always (see SEC2a) | URL opens in the browser |

**Web search backend.** S12 uses the user's existing self-hosted SearXNG instance on the LAN (currently `http://10.0.0.228:18082`). The address is set in the config file. The v1 setup's configuration (`pi/web-search.json` on the `pi` branch of the myvim repo) documents the reasoning behind the security rules in SEC3 to SEC5 and is worth reading before implementing the web skills.

## 5. Handoff requirement

**H1. Every result can be handed to the user.** All skills must be designed so the user can take over manually from wherever the model left off: view the PDF, open the website, step into the terminal, and so on. The model's work is never a dead end the user can only read about.

**H2. Two kinds of handoff.** Most skills hand off externally, using the application the user already works in. A few hand off to a view embedded inside the harness.

- **External handoff (default).** The harness opens the result in the appropriate outside application, for example a URL in the browser or a file in VS Code.
- **Embedded handoff.** The harness shows the result in a panel inside its own window. Known embedded views so far: terminal and image viewer.

**H3. Handoff on request.** The user can ask for a handoff in plain language ("open the terminal for me in the repos directory") and the model triggers it through the relevant skill.

## 6. Model backend

**M1. Local networked model.** The main model is a local LLM running on a dedicated machine on the local network, which already runs an LLM stack. The harness connects to that machine rather than to a cloud provider.

**M2. Summarizer agent.** Alongside the main model, a smaller local agent summarizes commands for user approval (see section 7). It runs on the harness machine if the hardware allows, falls back to the LLM machine if not, and if neither is available the harness shows "Summarizer model offline: \<reason>" where the summary would normally appear.

**M3. Model server.** The main model is served by llama.cpp's own server (not Lemonade or vLLM), which exposes an OpenAI-compatible API. The harness targets that API, and the server must be launched with tool calling enabled.

**M4. Network.** The model server listens on the LAN and the harness connects to it directly. The server address is set in the config file.

**M5. Memory is tuned by experiment.** Which models run where, and whether one model fills several roles (main, summarizer, web reader) in isolated contexts, cannot be known up front. These choices must be configurable so they can be adjusted iteratively based on what works best.

## 7. Security

Security has two main surfaces: terminal commands, and web traffic coming in and going out.

**SEC1. Command approval.** Every command run through the raw terminal skill requires user approval; there is no allow list. The harness shows the exact command plus a plain-language summary from the summarizer agent, since model-written commands are often long and hard to read. Commands the user would want to run freely are broken out as dedicated skills instead (see section 4).

**SEC1a. Approval never depends on the summarizer.** If the summarizer is unavailable, approval still works and the harness and terminal skill stay online; only the summary is replaced by the offline notice (see M2).

**SEC2. Prompt injection.** Because the model can read content from the internet, the harness must defend against prompt injection in that content. This was a major pain point in v1. The intended approach: a separate web reader model reads web content, summarizes it, and answers queries about it, so the main model works from that model's output rather than the raw content. The web reader is a different model from the main model and the summarizer. For now it is an off-the-shelf model; a model fine-tuned for this task may come later.

**SEC2a. Raw web content is gated.** The main model never sees raw web content except through the Get URL skill (S20), and every Get URL call requires user approval. The initial prompt at harness start tells the model that Get URL needs approval and that it should prefer the web reader skills whenever possible.

**SEC3. Web traffic stays local.** Lessons carried over from v1:

- Search goes only to the configured SearXNG instance. There is no fallback to hosted search providers: if SearXNG is down, search is down.
- The model cannot choose or override the search provider in a skill call.
- Search results and page text are never sent to a cloud model for summarizing. The local web reader model is the only thing that reads untrusted web content on the model's behalf.
- Pages are fetched directly over HTTP, never through hosted fetch or scraping services.
- Web skills never use browser cookies or browser profiles.

**SEC4. No LAN probing.** Web fetch skills refuse private network addresses (localhost, 10.x, 192.168.x, and so on) by default, so a manipulated model cannot use them to probe the local network. Exceptions are configured one host at a time (for example, just the SearXNG host), never whole subnets.

**SEC5. Untrusted locations.** Content that arrives from the web and lands on disk (downloaded files, cloned repositories) is kept in configured untrusted locations. The read-only file skills treat anything under those locations as untrusted web content: it goes through the web reader model rather than straight to the main model. Otherwise, a README in a cloned repo would bypass SEC2.

## 8. Sessions and history

**SH1.** Conversations are saved as sessions the user can return to.

**SH2.** Session history is searchable.

**SH3.** Memory that carries across sessions is out of scope for now and will be handled later.

## 9. Configuration

**CFG1. Single config file.** Configuration lives in one YAML file at `~/.harness/config.yml`.

**CFG2. Expose design parameters.** The config file breaks out as many design parameters as possible that could be useful to change, rather than hard-coding them. Examples include model endpoints, summarizer and web reader settings, log rollover size, theme colors, default panel sizes, the default editor and file manager for handoff, the Windows shell, and the harness's startup prompt.

## 10. Logging

**LOG1. Rolling log file.** The harness writes to a rolling log file.

**LOG2. Rollover size.** The log rolls over at 500 MB by default; the size is configurable.

**LOG3. Log channels.** Logging is split into channels, each written to its own file in `~/.harness/logs/`: prompts and responses; a skill-calling audit log; and internal logs from the harness code. The logging level of each channel is set in the config file.

## 11. User interface

**UI1. Theme.** The color theme is configurable. The default should avoid the current fashion of whites and beiges; lizard green and slate grey is the starting idea.

**UI2. Layout.** The default layout is a central chat surface surrounded by panels. Panels can be resized in every direction; rearranging them is not a priority.

**UI3. File explorer (top right).** A custom file explorer widget, not a standard tree view:

- Each file and directory is shown as an icon, arranged in an orbiting circle viewed almost in plane with the circle.
- Moving the mouse rolls the circle left or right.
- Expanding a directory smoothly moves it off to the side and shrinks it and its siblings, while its children come into view and grow to full size.

Inspiration is Screenvader, Stéphane Bourez's Flash portfolio site, featured on The FWA as FWA of the Day on 10 December 2005 (no longer working). Adam Shailer's study of it, https://www.adasha.com/lab/layouts/vader/, shows its mouse lens and depth-of-field blur.

**UI4. Chat sessions (bottom right).** A panel listing saved chat sessions.

**UI5. Viewer dock (far left).** A vertical dock running top to bottom along the far left edge, with macOS dock-style magnification: the icon in focus is largest and its neighbors scale down around it. For now the icons are the embedded viewers (terminal, image viewer, task list). Clicking an icon opens that viewer as a side panel on the left.

## 12. Additional requirements

Requirements an implementer needs, grouped by area. All items in this section are confirmed.

### Model interaction

**P1. Tool-calling format.** If the local model supports native tool calling (for example, OpenAI-style function calling), the harness uses it. If not, the harness defines a text format and parses tool calls out of the model's output. Depends on the Model API question.

**P2. Streaming.** Model responses stream into the chat as they are generated.

**P3. Stop.** The user can stop generation, or cancel a running skill, at any time.

**P4. Context limits.** When a session outgrows the model's context window, the harness summarizes older turns (compaction). Compaction can run automatically or be triggered manually by the user.

**P5. Model unavailable.** If the main model is unreachable, the harness stays open, shows a clear status, and saved sessions remain browsable.

### Skills and approval

**P6. Skill definition.** Each skill is a Python module declaring its name, the description the model sees, its parameters (as a JSON schema), whether it needs approval, and its handoff. Skills are discovered from a folder at startup, so adding one means dropping in a file.

**P7. Errors and timeouts.** A failing skill returns its error to the model as a result and never crashes the harness. Full error text is always written to the logs, never truncated, and is visible to the user, not just the model. Every skill call has a timeout, configurable per skill, so a blocked request fails quickly and visibly instead of hanging.

**P8. Approval UX.** Approval requests appear inline in the chat with approve, reject, and edit-then-approve options. A rejection is reported to the model, with an optional reason from the user.

**P9. Sensitive paths.** The harness keeps an explicit deny list of files and folders the model cannot read without explicit user approval. It ships with sensible defaults (for example `~/.ssh` and common credential files), and entries that don't exist on a given machine are ignored without error.

### Terminal

**P10. Shared terminal.** The model's commands run in the same terminal session the user gets on handoff, so the user inherits the command history and environment variables the model was working with.

**P11. Terminal capability.** The embedded terminal is a full terminal emulator, able to run full-screen programs such as vim or htop. It is built on an existing open source component, not written from scratch, so the harness inherits years of handling for the legacy edge cases real terminals deal with.

**Chosen approach:** xterm.js, the terminal component used by VS Code, running in a Qt web view and connected to a pseudo-terminal on each platform (the standard Unix pty on Linux, and pywinpty over Windows' ConPTY). Alternatives considered: QTermWidget has Python bindings but no Windows builds, and embedding another program's window only works on X11 and is fragile.

**P11a. Command output capture.** When the model runs a command in the shared terminal, the harness brackets it with invisible start and end markers (escape sequences the terminal hides), with the end marker carrying the exit code. The harness captures exactly the text between the markers as the command's output and returns it, with the exit code, to the model. This is the shell-integration technique VS Code's terminal uses, and it must work in both bash and PowerShell.

**P12. Working directory.** Each session has a current working directory, shown in the UI. Skills resolve relative paths against it.

### Application

**P13. Responsive UI.** Model calls and skills never block the UI; the window stays responsive while work runs in the background.

**P14. Session storage.** Sessions are stored locally under `~/.harness/` in a format that supports the full-text search SH2 requires (for example, a single SQLite database).

**P15. Chat rendering.** Chat messages render Markdown, including syntax-highlighted code blocks.

**P16. First run.** On first launch the harness creates `~/.harness/` with a commented default config. An invalid config produces a clear error that names the bad setting.

**P17. Testing.** Two test suites:

- **Unit tests** with a fake model, so skills and the approval flow can be tested without the LLM server.
- **Stack tests** that run against the user's actual config file, to check that the chosen model stack meets the harness's requirements (for example, that the servers are reachable and tool calling works).

**P18. Install and launch.** The harness is pip-installable and provides a CLI command that launches it. There is no standalone Windows executable.

## 13. Milestones

**Milestone 1: Foundation.** The UI layout is in place, with stubs for anything planned but not yet built; in particular, the file explorer is a standard tree view for now. Chat and model bring-up are complete. Logging is implemented. The config file is started, though not every setting exists yet.

**Milestone 2: First skills.** Skill integration with the model, starting with the low-hanging skills only.

**Milestone 3 and later.** The remaining skills, one large skill at a time; embedded views implemented; models chosen and deployed; the orbiting file explorer and the dock polished.

## 14. Open questions

None at this time.
