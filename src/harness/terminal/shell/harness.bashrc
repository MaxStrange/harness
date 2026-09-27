# Loaded by the harness with `bash --rcfile` (P10, P11a). It sources the user's own
# ~/.bashrc first, then installs two hooks that bracket every command with
# invisible markers (OSC 7331) so the harness can capture exactly the output of
# the commands it runs and read the exit code. xterm.js ignores the sequences.
if [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi

__harness_pre() { printf '\033]7331;S\007'; }
__harness_post() { local __code=$?; printf '\033]7331;E;%s\007' "$__code"; return $__code; }

# PS0 is expanded after a command is read and before it runs.
PS0='$(__harness_pre)'"${PS0}"
# PROMPT_COMMAND runs before each prompt; ours goes first so $? is still the command's.
if [ -n "${PROMPT_COMMAND}" ]; then
    PROMPT_COMMAND="__harness_post; ${PROMPT_COMMAND}"
else
    PROMPT_COMMAND="__harness_post"
fi
export HARNESS_TERMINAL=1
