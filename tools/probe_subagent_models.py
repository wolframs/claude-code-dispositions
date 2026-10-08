"""Which model does a spawned subagent ACTUALLY get? Measured, not inferred.

    python3 tools/probe_subagent_models.py                 # the standard matrix
    python3 tools/probe_subagent_models.py <parent> <type> # one cell
    python3 tools/probe_subagent_models.py --delegation-check [parent] [type]

COSTS NOTHING. It borrows ccctl's delivered-capture trick: point
ANTHROPIC_BASE_URL at a local socket and scrub every ANTHROPIC_*/CLAUDE* var
out of the child, so the session cannot reach the API and carries no key. The
parent's first turn is scripted as an `Agent` tool_use; Claude Code then spawns
the subagent for real, and its OWN request arrives at the same socket with its
`model` field intact.

Why this exists: the pipeline otherwise only INFERS a subagent's model, from
the pin plus the bundle's alias table. That inference was wrong about `Plan` —
an explicit `model:"inherit"` outranks the pin, while an ABSENT field does not
— and nothing else in the repo could have caught it. See spec/40 and
notes/2026-09-22-subagent-model-routing.md.

Two things make a run trustworthy, and both are easy to get wrong:

  * `--setting-sources project,local` drops ~/.claude/settings.json, which is
    the only way to see the machine's behaviour WITHOUT the operator's pin. A
    run that forgets it cannot tell a cap from a pin — both produce opus here.
  * parent and subagent are told apart by the session id in the billing header,
    NOT by whether the request carries the Agent tool. `general-purpose` can
    spawn agents too, so the tool-presence test silently mislabels it.
"""
import http.server, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, threading
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ccctl", str(Path(__file__).resolve().with_name("ccctl.py")))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def sse(events):
    return "".join(f"event: {t}\ndata: {json.dumps(d)}\n\n" for t, d in events)


def reply(n, model, block, stop, stream):
    """One scripted assistant turn: `block` is a content block, `stop` the reason."""
    if not stream:
        return json.dumps({"id": f"msg_p{n}", "type": "message", "role": "assistant",
                           "model": model, "content": [block], "stop_reason": stop,
                           "usage": {"input_tokens": 1, "output_tokens": 1}}), "application/json"
    start = {"type": "message_start", "message": {
        "id": f"msg_p{n}", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None,
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
    if block["type"] == "tool_use":
        blocks = [
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "tool_use", "id": block["id"],
                                                       "name": block["name"], "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "input_json_delta",
                                               "partial_json": json.dumps(block["input"])}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0})]
    else:
        blocks = [
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": block["text"]}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0})]
    return sse([("message_start", start), *blocks,
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                                   "usage": {"output_tokens": 1}}),
                ("message_stop", {"type": "message_stop"})]), "text/event-stream"


def probe(parent_model, subagent_type, timeout=180, extra_argv=(), extra_env=None, spawn_model=None):
    got, cwd = [], tempfile.mkdtemp(prefix="ccctl-probe-")
    spawned = {"done": False}

    class Sink(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, payload, kind):
            raw = payload.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.send(404, json.dumps({"type": "error", "error": {
                "type": "not_found_error", "message": "probe"}}), "application/json")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw)
            except ValueError:
                return self.do_GET()
            if "/v1/messages" not in self.path or "count_tokens" in self.path:
                return self.do_GET()
            main = bool(body.get("system")) and bool(body.get("tools"))
            if not main:
                return self.send(200, *reply(0, body.get("model", parent_model),
                                             {"type": "text", "text": "ok"}, "end_turn",
                                             bool(body.get("stream"))))
            got.append(body)
            names = [t.get("name") for t in (body.get("tools") or [])]
            is_parent = "Agent" in names and not spawned["done"]
            stream = bool(body.get("stream"))
            if is_parent:
                spawned["done"] = True
                block = {"type": "tool_use", "id": "toolu_probe_01", "name": "Agent",
                         "input": {"description": "probe", "subagent_type": subagent_type,
                                   "prompt": "Reply with the single word: ok",
                                   **({"model": spawn_model} if spawn_model else {})}}
                return self.send(200, *reply(len(got), body.get("model", parent_model),
                                             block, "tool_use", stream))
            # the subagent (or a later parent turn): end it
            return self.send(200, *reply(len(got), body.get("model", parent_model),
                                         {"type": "text", "text": "ok"}, "end_turn", stream))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"
    env["ANTHROPIC_API_KEY"] = "probe"
    env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-ts"
    env.update(extra_env or {})
    argv = [str(cc.claude_binary()), "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", parent_model, "--max-turns", "4",
            "--permission-mode", "bypassPermissions",
            "--allow-dangerously-skip-permissions", "--no-session-persistence"] + list(extra_argv)
    stdin = json.dumps({"type": "user", "parent_tool_use_id": None,
                        "message": {"role": "user", "content": "go"}}) + "\n"
    try:
        r = subprocess.run(argv, input=stdin, env=env, cwd=cwd,
                           capture_output=True, text=True, timeout=timeout)
        err = None if r.returncode == 0 else (r.stderr or "")[-400:]
    except subprocess.TimeoutExpired:
        err = f"timeout after {timeout}s"
    finally:
        srv.shutdown(); srv.server_close(); shutil.rmtree(cwd, ignore_errors=True)
    return got, err


NO_USER_SETTINGS = ("--setting-sources", "project,local")


def system_text(req):
    s = req.get("system")
    if isinstance(s, list):
        s = " ".join(b.get("text", "") for b in s if isinstance(b, dict))
    return s or ""


def session_id(req):
    """The per-session suffix CC puts in its billing header. The ONLY reliable
    way to tell the subagent's request from the parent's: `general-purpose`
    carries the Agent tool too, so testing for that tool mislabels it."""
    m = re.search(r"cc_version=[\d.]+\.(\w+)", system_text(req))
    return m.group(1) if m else "?"


def subagent_model(parent_model, subagent_type, use_pin, spawn_model=None):
    """The model the spawned subagent's own request carried, or None."""
    kw = {} if use_pin else {"extra_argv": NO_USER_SETTINGS}
    reqs, err = probe(parent_model, subagent_type, spawn_model=spawn_model, **kw)
    if not reqs:
        return None, err or "no requests captured"
    root = session_id(reqs[0])
    spawned = [r for r in reqs if session_id(r) != root]
    return (spawned[0].get("model") if spawned else None), err


def matrix(parent_model):
    for use_pin in (False, True):
        print(f"### parent = {parent_model}, "
              f"{'WITH the machine pin' if use_pin else 'NO pin (user settings excluded)'}")
        for t in ("Explore", "Plan", "general-purpose", "claude"):
            got, err = subagent_model(parent_model, t, use_pin)
            print(f"  {t:18s} -> {got or f'NO SUBAGENT ({err})'}")


def delegation_check(parent_model, subagent_type):
    reqs, err = probe(parent_model, subagent_type)
    if err or not reqs:
        print(f"FAIL capture: {err or 'no requests'}")
        return False
    root = session_id(reqs[0])
    if root == "?":
        print("FAIL capture: no parent session identity")
        return False
    seen, passed, child_seen = set(), True, False
    for req in reqs:
        sid = session_id(req)
        if sid in seen:
            continue
        seen.add(sid)
        child = sid != root
        child_seen |= child
        description = next((t.get("description", "") for t in req.get("tools", [])
                            if t.get("name") in ("Agent", "Task")), None)
        ok = (description is None and child) or (
            description is not None and cc.SUBAGENT_DELEGATION_OPT_IN in description)
        passed &= ok and sid != "?"
        print(f"{'PASS' if ok else 'FAIL'} {'child' if child else 'parent'} {req.get('model')}: "
              + ("no agent tool" if description is None else "subagent-only opt-in "
                 + ("delivered" if ok else "MISSING")))
    if not child_seen:
        print("FAIL capture: no child request")
    return passed and child_seen


if __name__ == "__main__":
    if sys.argv[1:2] == ["--delegation-check"]:
        sys.exit(0 if delegation_check(sys.argv[2] if len(sys.argv) > 2 else "claude-fable-5-1",
                                      sys.argv[3] if len(sys.argv) > 3 else "general-purpose") else 1)
    elif len(sys.argv) > 2:
        got, err = subagent_model(sys.argv[1], sys.argv[2], use_pin=True)
        print(f"{sys.argv[2]} from {sys.argv[1]} -> {got or f'NO SUBAGENT ({err})'}")
    else:
        matrix(sys.argv[1] if len(sys.argv) > 1 else "claude-fable-5-1")
