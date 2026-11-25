"""Real hidden-process lifecycle, with an isolated dummy service and no integrations."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import run_process


@unittest.skipUnless(os.name == "nt", "Windows process launch is required")
class StartScriptTests(unittest.TestCase):
    def test_start_survives_caller_exit_rejects_duplicate_and_stops_effective_child(self):
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="arion start test-") as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
                           check=True, capture_output=True, timeout=60)
            runner = root / "deploy" / "windows" / "run_process.py"
            runner.parent.mkdir(parents=True)
            service = root / "dummy_service.py"
            (root / "deploy/windows/arion-common.ps1").write_bytes((scripts / "arion-common.ps1").read_bytes())
            service.write_text("import time\nprint('Worker iniciado.', flush=True)\nwhile True: time.sleep(0.1)\n", encoding="utf-8")
            site_packages = Path(importlib.util.find_spec("dotenv").origin).parent.parent
            runner.write_text(
                "import importlib.util, pathlib, sys\n"
                f"sys.path.insert(0, {str(site_packages)!r})\n"
                f"spec = importlib.util.spec_from_file_location('supervisor', {str(scripts / 'run_process.py')!r})\n"
                "r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)\n"
                "r.__file__ = __file__\n"
                "r._preflight = lambda role='web': 0\n"
                f"r.PROJECT_ROOT = pathlib.Path({str(root)!r})\n"
                "r.RUN_ROOT = r.PROJECT_ROOT / 'var/run'\n"
                "r.LOG_ROOT = r.PROJECT_ROOT / 'var/logs'\n"
                f"r._command = lambda role: [sys.executable, {str(service)!r}]\n"
                "raise SystemExit(r.guarded_main())\n", encoding="utf-8")
            # supervise() uses this working directory, without loading Django.
            (root / "arion_geo").mkdir()

            def invoke(name):
                # Real detached children can inherit the caller's pipe handles;
                # file output lets the invoking terminal exit independently.
                output = root / "powershell-call.log"
                with output.open("wb") as stream:
                    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(scripts / name), "-Role", "worker", "-ProjectRoot", str(root)],
                        stdout=stream, stderr=subprocess.STDOUT, timeout=50)
                return SimpleNamespace(returncode=result.returncode,
                    stdout=output.read_text(encoding="utf-8", errors="replace"), stderr="")

            try:
                started = invoke("start-arion.ps1")
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                record = json.loads((root / "var/run/worker.child.json").read_text())
                self.assertTrue(run_process._pid_is_running(record["pid"], record["created_at"]))
                again = invoke("start-arion.ps1")
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                self.assertEqual(json.loads((root / "var/run/worker.child.json").read_text())["pid"], record["pid"])
                stopped = invoke("stop-arion.ps1")
                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                self.assertFalse(run_process._pid_is_running(record["pid"], record["created_at"]))
                self.assertFalse((root / "var/run/worker.child.json").exists())
            finally:
                invoke("stop-arion.ps1")

    def test_early_worker_exit_never_reports_running_and_retries_are_bounded(self):
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="arion failure test-") as directory:
            root = Path(directory)
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
                           check=True, capture_output=True, timeout=60)
            (root / "arion_geo").mkdir()
            runner = root / "deploy/windows/run_process.py"
            runner.parent.mkdir(parents=True)
            fault = root / "fault.py"
            fault.write_text("import time\nprint('Worker iniciado.', flush=True)\ntime.sleep(.2)\n"
                             "raise RuntimeError('password=secret-test-value')\n", encoding="utf-8")
            packages = Path(importlib.util.find_spec("dotenv").origin).parent.parent
            runner.write_text(
                "import importlib.util, pathlib, sys\n"
                f"sys.path.insert(0, {str(packages)!r})\n"
                f"spec=importlib.util.spec_from_file_location('supervisor', {str(scripts / 'run_process.py')!r})\n"
                "r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)\n"
                "r.__file__=__file__;r.MAX_RESTARTS=3\n"
                f"r.PROJECT_ROOT=pathlib.Path({str(root)!r})\n"
                "r.RUN_ROOT=r.PROJECT_ROOT/'var/run';r.LOG_ROOT=r.PROJECT_ROOT/'var/logs'\n"
                "r._preflight=lambda role='web': 0\n"
                f"r._command=lambda role: [sys.executable, {str(fault)!r}]\n"
                "raise SystemExit(r.guarded_main())\n", encoding="utf-8")
            output = root / "call.log"
            with output.open("wb") as stream:
                started = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(scripts / "start-arion.ps1"), "-Role", "worker", "-ProjectRoot", str(root)],
                    stdout=stream, stderr=subprocess.STDOUT, timeout=50)
            self.assertNotEqual(started.returncode, 0)
            with mock_root(run_process, root):
                status = run_process._status("worker")
                self.assertEqual((status["state"], status["restarts"]), ("failed", 3))
                self.assertFalse(status["locked"])
            log = (root / "var/logs/arion-worker.log").read_text(encoding="utf-8")
            self.assertIn("RuntimeError", log)
            self.assertNotIn("secret-test-value", log)


def mock_root(module, root):
    from unittest.mock import patch
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.object(module, "RUN_ROOT", root / "var/run"))
    stack.enter_context(patch.object(module, "LOG_ROOT", root / "var/logs"))
    return stack


if __name__ == "__main__":
    unittest.main()
