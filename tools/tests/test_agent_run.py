"""Exercise literal argv/stdin, arbitrary cwd, timeout and noninteractive setup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import agent_run as ar


class AgentRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-run-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "Downloads with spaces"
        self.cwd.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"default_cwd": str(self.root), "timeout": 10}), encoding="utf-8")

    def call(self, *args, **kwargs):
        return subprocess.run([sys.executable, str(TOOLS / "agent_run.py"), "--config", str(self.config), *args],
                              capture_output=True, text=True, timeout=20, **kwargs)

    def test_literal_argv_and_arbitrary_directory(self):
        literal = 'spaces "quotes"; $(touch SHOULD_NOT_EXIST) `echo no` & |'
        result = self.call("exec", "--cwd", str(self.cwd), "--", sys.executable, "-c",
                           "import os,sys,json;print(json.dumps([os.getcwd(),sys.argv[1]]))", literal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.cwd), literal])
        self.assertFalse((self.cwd / "SHOULD_NOT_EXIST").exists())

    def test_json_request_stdin_env_and_write(self):
        code = "import os,sys,pathlib;pathlib.Path('result').write_text(sys.stdin.read()+os.environ['TASK_VALUE'])"
        payload = {"argv": [sys.executable, "-c", code], "cwd": str(self.cwd),
                   "env": {"TASK_VALUE": "suffix"}, "stdin": "quoted ' $ input\n"}
        result = self.call("request", input=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.cwd / "result").read_text(), "quoted ' $ input\nsuffix")

    def test_json_request_accepts_powershell_utf8_bom(self):
        payload = {"argv": [sys.executable, "-c", "import sys;print(sys.argv[1])", 'literal "$HOME"'],
                   "cwd": str(self.cwd)}
        result = subprocess.run(
            [sys.executable, str(TOOLS / "agent_run.py"), "--config", str(self.config), "request"],
            input=json.dumps(payload).encode("utf-8-sig"), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip(), 'literal "$HOME"')

    def test_exit_status_and_timeout(self):
        self.assertEqual(self.call("exec", "--", sys.executable, "-c", "raise SystemExit(7)").returncode, 7)
        result = self.call("exec", "--timeout", "0.2", "--", sys.executable, "-c", "import time;time.sleep(30)")
        self.assertEqual(result.returncode, 124, result.stderr)

    def test_missing_directory_and_invalid_argv(self):
        result = self.call("request", input=json.dumps({"argv": [], "cwd": str(self.root)}))
        self.assertEqual(result.returncode, 2)
        result = self.call("exec", "--cwd", str(self.root / "absent"), "--", sys.executable, "-V")
        self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(os.name == "posix", "POSIX process group isolation")
    def test_timeout_stops_descendants_but_not_an_unrelated_process(self):
        grandchild = self.root / "grandchild-survived"
        sibling = self.root / "sibling-survived"
        write_later = "import time,pathlib;time.sleep(1);pathlib.Path(%r).write_text('alive')"
        other = subprocess.Popen([sys.executable, "-c", write_later % str(sibling)], start_new_session=True)
        self.addCleanup(lambda: other.wait(timeout=5))
        script = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',%r]);time.sleep(30)"
        result = self.call("exec", "--timeout", "0.3", "--", sys.executable, "-c",
                           script % (write_later % str(grandchild)))
        self.assertEqual(result.returncode, 124, result.stderr)
        other.wait(timeout=5)
        time.sleep(0.3)
        self.assertTrue(sibling.exists())
        self.assertFalse(grandchild.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX installation/process groups")
    def test_install_without_shell_startup_and_preserve_config(self):
        env = dict(os.environ, HOME=str(self.root))
        (self.root / ".zshrc").write_text("touch SHELL_STARTUP_RAN\n", encoding="utf-8")
        installed = subprocess.run([sys.executable, str(TOOLS / "agent_run.py"), "install"],
                                   env=env, capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        config = self.root / ".config/agent-run/config.json"
        body = json.loads(config.read_text())
        body["timeout"] = 123
        config.write_text(json.dumps(body))
        again = subprocess.run([sys.executable, str(TOOLS / "agent_run.py"), "install"], env=env,
                               capture_output=True, text=True)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(config.read_text())["timeout"], 123)
        result = subprocess.run([str(self.root / ".local/bin/agent-run"), "doctor"], env=env,
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "SHELL_STARTUP_RAN").exists())
        self.assertTrue(list((self.root / ".local/lib/agent-run").glob("*.previous-*")))


if __name__ == "__main__":
    unittest.main()
