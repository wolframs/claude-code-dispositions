#!/usr/bin/env python3
"""Predictable POSIX agent entrypoint. Convenience, not a permission boundary."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def config_path():
    return Path.home() / ".config/agent-run/config.json"


def expand(value):
    return Path(value).expanduser().absolute()


def defaults():
    home = Path.home()
    return {
        "default_cwd": str(home),
        "timeout": 900,
        "path": [str(home / ".local/bin"), str(home / ".local/npm-global/bin"),
                 str(home / ".cargo/bin"), str(home / ".bun/bin"),
                 "/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin",
                 "/usr/local/sbin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"],
        "ccctl_workspace": str(home / "ccctl"),
        "locations": {name: str(home / name) for name in
                      ("Projects", "repos", "apps", "Downloads", "Desktop")
                      if (home / name).is_dir()},
    }


def load_config(path=None):
    cfg = defaults()
    path = Path(path) if path else config_path()
    if path.exists():
        supplied = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("agent-run config must be a JSON object")
        cfg.update(supplied)
    return cfg


def environment(cfg, overrides=None):
    env = dict(os.environ)
    # Configuration contains directories, never shell code. Follow the user's
    # stable node link so npm's sibling executables track runtime upgrades.
    paths = [str(expand(p)) for p in cfg["path"]]
    node = Path.home() / ".local/bin/node"
    if node.is_file():
        paths.insert(1, str(node.resolve().parent))
    npmrc = Path.home() / ".npmrc"
    if npmrc.is_file():
        for line in npmrc.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "prefix":
                value = value.strip().strip("\"'").replace("${HOME}", str(Path.home()))
                if value:
                    paths.insert(1, str(expand(value) / "bin"))
    env["PATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if Path(p).is_dir()))
    if overrides:
        if not isinstance(overrides, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                     for k, v in overrides.items()):
            raise ValueError("env must map names to strings")
        env.update(overrides)
    return env


def stop_group(proc):
    """Stop only the process group created by this invocation, never siblings."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        proc.kill()
    proc.wait()


def run(argv, cwd, env, timeout, stdin=None, stdout=None, stderr=None):
    if not argv or not all(isinstance(a, str) and "\0" not in a for a in argv):
        raise ValueError("argv must be a nonempty array of strings")
    if not isinstance(timeout, (int, float)) or timeout < 0:
        raise ValueError("timeout must be nonnegative; 0 disables it")
    cwd = expand(cwd)
    if not cwd.is_dir():
        raise ValueError("working directory does not exist: " + str(cwd))
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin, stdout=stdout,
                            stderr=stderr, start_new_session=os.name == "posix")
    previous = {}

    def interrupted(signum, _frame):
        raise KeyboardInterrupt("signal " + str(signum))

    try:
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            if sig not in previous:
                previous[sig] = signal.signal(sig, interrupted)
        try:
            code = proc.wait(timeout=timeout or None)
            return code if code >= 0 else 128 - code
        except subprocess.TimeoutExpired:
            stop_group(proc)
            print("agent-run: timeout after %ss" % timeout, file=sys.stderr)
            return 124
        except BaseException:
            stop_group(proc)
            raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install():
    if os.name != "posix":
        raise ValueError("installation is for Linux/macOS; invoke the source directly for tests")
    home, source = Path.home(), Path(__file__).resolve().parent
    lib = home / ".local/lib/agent-run"
    wrappers = {name: home / ".local/bin" / name for name in ("agent-run", "cc-deploy")}
    tag = "# agent-run managed entrypoint"
    for path in wrappers.values():
        if path.exists() and tag not in path.read_text(encoding="utf-8"):
            raise ValueError("refusing to replace an unrelated entrypoint: " + str(path))
    stamp = str(time.time_ns())
    bootstrap = "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable
    for name in ("agent_run.py", "cc_deploy.py"):
        target = lib / name
        if target.exists():
            shutil.copy2(target, lib / (name + ".previous-" + stamp))
        atomic_write(target, (source / name).read_bytes())
    for name, script in (("agent-run", "agent_run.py"), ("cc-deploy", "cc_deploy.py")):
        body = "#!/bin/sh\n" + tag + "\nexec " + shlex.quote(bootstrap) + " -u "
        body += shlex.quote(str(lib / script)) + ' "$@"\n'
        atomic_write(wrappers[name], body.encode(), 0o755)
    if not config_path().exists():
        atomic_write(config_path(), (json.dumps(defaults(), indent=2) + "\n").encode())
    print("Installed agent-run and cc-deploy in " + str(home / ".local/bin"))
    print("Configuration: " + str(config_path()) + " (existing configuration preserved)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("install", help="install stable user-level entrypoints; preserve existing config")
    commands.add_parser("doctor", help="JSON paths and tools, without dumping environment secrets")
    commands.add_parser("request", help="read one JSON request from stdin (argv, cwd, timeout, env, stdin)")
    execute = commands.add_parser("exec", help="run argv directly; arbitrary commands and directories")
    execute.add_argument("--cwd")
    execute.add_argument("--timeout", type=float)
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.action == "install":
        install()
        return 0
    cfg = load_config(args.config)
    env = environment(cfg)
    if args.action == "doctor":
        print(json.dumps({"config": str(args.config or config_path()),
                          "default_cwd": cfg["default_cwd"], "timeout": cfg["timeout"],
                          "path": env["PATH"].split(os.pathsep), "locations": cfg["locations"],
                          "ccctl_workspace": cfg["ccctl_workspace"],
                          "tools": {t: shutil.which(t, path=env["PATH"]) for t in
                                    ("git", "python3", "python3.11", "uv", "node", "npm",
                                     "pnpm", "bun", "tweakcc", "claude", "cc-deploy")}}, indent=2))
        return 0
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig")) if args.action == "request" else {
        "argv": args.argv[1:] if args.argv[:1] == ["--"] else args.argv,
        "cwd": args.cwd or cfg["default_cwd"],
        "timeout": cfg["timeout"] if args.timeout is None else args.timeout}
    env = environment(cfg, payload.get("env"))
    # A JSON request owns stdin; explicit content is passed in a temporary file
    # to avoid pipe deadlocks with large input. exec otherwise inherits stdin.
    with tempfile.TemporaryFile() as input_file:
        if "stdin" in payload:
            input_file.write(payload["stdin"].encode("utf-8"))
            input_file.seek(0)
        return run(payload["argv"], payload.get("cwd", cfg["default_cwd"]), env,
                   payload.get("timeout", cfg["timeout"]),
                   stdin=input_file if args.action == "request" else None)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("agent-run: " + str(exc), file=sys.stderr)
        sys.exit(2)
