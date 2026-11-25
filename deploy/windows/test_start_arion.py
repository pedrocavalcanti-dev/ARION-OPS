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
        with tempfile.TemporaryDirectory(prefix="arion-start-test-") as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
                           check=True, capture_output=True, timeout=60)
            runner = root / "deploy" / "windows" / "run_process.py"
            runner.parent.mkdir(parents=True)
            service = root / "dummy_service.py"
            service.write_text("import time\nwhile True: time.sleep(0.1)\n", encoding="utf-8")
            site_packages = Path(importlib.util.find_spec("dotenv").origin).parent.parent
            runner.write_text(
                "import importlib.util, pathlib, sys\n"
                f"sys.path.insert(0, {str(site_packages)!r})\n"
                f"spec = importlib.util.spec_from_file_location('supervisor', {str(scripts / 'run_process.py')!r})\n"
                "r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)\n"
                "r.__file__ = __file__\n"
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


if __name__ == "__main__":
    unittest.main()
