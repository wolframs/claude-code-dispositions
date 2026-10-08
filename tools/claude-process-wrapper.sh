#!/bin/sh
# Runs the machine's installed (patched) Claude Code in place of a bundled one.
#
# For hosts that launch Claude Code as `<wrapper> <their bundled claude> ARGS...`:
# the VS Code extension's `claudeCode.claudeProcessWrapper` setting does exactly
# that (extension 2.1.293: command = wrapper, args = [bundled path, ...args]).
# The bundled copy is stock and pinned to the extension release; the installed
# one carries this repository's patches. Read spec/40, "Bundled copies of
# Claude Code", before pointing anything else here.
#
# The first argument is dropped only when it is an executable named `claude`,
# so a host that calls the wrapper without a bundled path loses nothing.
# CLAUDE_CODE_CLI overrides the target.
target="${CLAUDE_CODE_CLI:-$HOME/.local/bin/claude}"
case "$1" in
  */claude|*/claude.exe) [ -x "$1" ] && shift ;;
esac
exec "$target" "$@"
