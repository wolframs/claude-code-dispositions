# Noninteractive agent execution and CC deployment

Operator scope, 2026-09-28: make SSH predictable and deployment recoverable,
without restricting agents to repositories or removing their ability to change
Python, Node, packages or configuration. Downloads and Desktop are legitimate
working directories. Authentication and access restrictions are a separate
operator concern. These tools are conveniences, **not a sandbox or security
boundary**. Normal Unix permissions and the task's authorization still apply.

## Entrypoints

Installed on the Linux and Mac fleet as `~/.local/bin/agent-run` and
`~/.local/bin/cc-deploy`. Use the explicit path over SSH: the remote default
shell does not put `~/.local/bin` on PATH. Neither command loads `.zshrc`,
`.zprofile`, a login shell, tmux or the interactive secrets file. SSH still
uses the account's configured shell to launch the entrypoint, as usual.

Sources live here because this repository owns the CC deployment workflow:
`tools/agent_run.py`, `tools/cc_deploy.py`. The generic executor is usable for
any project; it does not import CC tooling. Dotfiles continue to own interactive
shell configuration, which this installation does not change.

```sh
ssh -o BatchMode=yes [HUMAN]@HOST '~/.local/bin/agent-run doctor'
ssh -o BatchMode=yes [HUMAN]@HOST '~/.local/bin/agent-run exec --cwd ~/Projects/MY_REPO -- git status --short'
ssh -o BatchMode=yes [HUMAN]@HOST '~/.local/bin/agent-run exec --cwd ~/Downloads -- python3 -V'
ssh -o BatchMode=yes [HUMAN]@HOST '~/.local/bin/cc-deploy status'
```

`agent-run exec` takes literal arguments after `--`, streams stdin/stdout/stderr
unchanged, and returns the command's exit status. Any working directory is
allowed. Shell syntax is not interpreted unless the caller explicitly runs a
shell, for example `-- bash -c '...'`. Builds, package installation, `uv`, `npm`,
configuration edits, and ordinary user-authorized administration remain
available. Project virtual environments are selected explicitly (`uv run`,
`.venv/bin/python`, etc.); changing cwd does not silently activate one.

For spaces and quoting across PowerShell, SSH and the remote shell, pass JSON
on stdin to `agent-run request`:

```json
{
  "argv": ["python3", "-c", "import os; print(os.getcwd())"],
  "cwd": "~/Desktop",
  "timeout": 120,
  "env": {"TASK_OPTION": "a literal value"},
  "stdin": "optional input for the child"
}
```

Send UTF-8 JSON from a file or pipe (a PowerShell byte-order mark is accepted);
request mode consumes it and supplies only
the explicit `stdin` string (empty by default) to the command. No command
arguments or environment values are logged by the generic executor. `doctor`
reports tool paths and configured directories, not environment secrets.

The default command timeout is 900 seconds, overridable with `--timeout` or the
JSON field; `0` disables it for an intentionally long operation. A timeout
returns 124 and terminates the process group created for that command. Signals
and interruption also clean up that group, not unrelated sessions. A command
that deliberately daemonizes into another session is outside that group; use
the service's own lifecycle controls for such processes.

## Configuration and installation

`~/.config/agent-run/config.json` owns the default cwd, timeout, PATH directories,
informational location list, and `ccctl_workspace`. `--config PATH` selects an
alternative file. No path in `locations` is an allowlist. Configuration changes
take effect on the next invocation; existing processes are untouched.

PATH is explicit rather than inherited from shell startup. The executor adds
the existing node symlink's resolved bin directory and the prefix from
`~/.npmrc`, so a runtime change behind the user's stable link is observed on
the next invocation. User executables precede system ones. Add other runtime
manager shims/directories to `path` as needed; their initialization is not run
implicitly. Child processes otherwise inherit the SSH environment, with any
explicit request overrides. Credentials must be supplied through an appropriate
environment or tool credential store, not by sourcing the interactive shell.

Install/update from the reviewed checkout:

```sh
/usr/bin/python3 ~/ccctl/repo/tools/agent_run.py install
```

This installs stable copies under `~/.local/lib/agent-run/` plus small launchers
using the system Python. It preserves existing configuration and keeps the
previous script copies when updating. It refuses to overwrite unrelated
entrypoints. A repository pull does not implicitly update these installed
copies: run `install` deliberately when the tools change. Neither the SSH
daemon, authorized keys, sudo policy nor interactive shell files are modified.

Uninstall by removing the two managed launchers and their private library
directory; retain or remove the configuration separately. Saved CC binary
rollbacks and deployment logs are in `~/ccctl/`, independent of installation.

## CC deployment commands

```sh
cc-deploy status
cc-deploy verify
cc-deploy apply-reviewed-commit FULL_40_CHARACTER_SHA
cc-deploy backups
cc-deploy rollback BACKUP_ID
```

This is a **same-CC-version prompt deployment** interface. It delegates to the
existing `ccctl` staged apply rather than introducing another binary patcher.
Release upgrades still follow spec/40 and its per-release review. Supplying a
SHA states which commit was reviewed; the tool does not claim to prove human
review. `apply-reviewed-commit`:

1. Requires a clean, attached deployment checkout; reports the current status.
2. Fetches `origin` (unless `--no-fetch`) and requires the exact requested commit
   to be a fast-forward from HEAD and reachable from the configured upstream.
3. Copies and hashes the current patched executable and saves `ccctl-state.json`
   under `~/ccctl/agent-rollbacks/ID/`. It records an in-progress transaction.
4. Fast-forwards to that exact commit and runs `tools/check_repo.py`. Older
   sparse deployments gain `baseline/` and `ab/` if needed for validation;
   the transcript corpus is not pulled into the checkout.
5. Runs `ccctl.py apply --no-pull`, then `status --check`, all configured
   `status --delivered` shapes, the parent/child delegation capture, and flags.
6. Records success, or restores the saved executable and ccctl state if failure
   occurred after the binary changed. The wrapper does not signal or restart
   existing Claude sessions. New sessions use the activated file. CC's own
   daemon can react to a launcher change by restarting and adopting workers;
   atomic placement does not guarantee zero process effects (spec/40).

Every step is logged under `~/ccctl/agent-deploy-logs/`, with the path printed
at startup. The wrapper reports each running step; stdout from checks is in
that log. Default per-step timeout is 1800 seconds (`--timeout`, `0` disables).
An advisory lock excludes concurrent `cc-deploy` operations. Direct invocation
of old `ccctl` commands does not take that lock: do not run them concurrently.

Backups are retained, never automatically pruned. `rollback` verifies the
selected hash and launcher target, saves an undo point, and copies the saved
executable into place by atomic replacement. It never overwrites a running
executable's inode. A backup from a different launcher target/version is
refused rather than guessed at. macOS's embedded signature/entitlements are
preserved with the copied binary; normal apply still performs ccctl's signing.

Rollback restores the **binary and ccctl state**, not the repository checkout,
Claude settings, or installed packages. Source files may be used by another
session, so the wrapper never resets or force-checks-out the repository. If
source and binary then differ, the tripwire can correctly report that fact;
`cc-deploy status` also shows the rollback record. Fix forward by applying a
reviewed compatible commit. If an SSH client is forcibly killed or power fails,
the in-progress record and named backup remain for inspection/manual recovery.
