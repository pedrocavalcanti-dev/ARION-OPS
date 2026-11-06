"""Focused Windows process identity tests for the ARION supervisor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import run_process


@unittest.skipUnless(os.name == "nt", "Windows process APIs are required")
class WindowsProcessIdentityTests(unittest.TestCase):
    def test_native_api_reads_current_process_and_rejects_missing_pid(self):
        identity = run_process._process_identity(os.getpid())
        self.assertIsNotNone(identity)
        self.assertGreater(identity["created_at"], 0)
        self.assertTrue(identity["executable"].lower().endswith(".exe"))
        self.assertTrue(run_process._pid_is_running(os.getpid(), identity["created_at"]))
        self.assertFalse(run_process._pid_is_running(0xFFFFFFFE))
        completed = subprocess.Popen([identity["executable"], "-c", "pass"])
        completed.wait(timeout=10)
        self.assertGreater(run_process._windows_process_created_at(completed._handle), 0)

    def test_unlocked_role_reports_stopped(self):
        with mock.patch.object(run_process, "_is_locked", return_value=False):
            with mock.patch("builtins.print") as output:
                self.assertEqual(run_process._print_status("worker"), 1)
        output.assert_called_once_with("worker: stopped")

    def test_reused_pid_does_not_match_saved_creation_time(self):
        current = run_process._process_identity(os.getpid())
        self.assertIsNotNone(current)
        self.assertFalse(run_process._pid_is_running(os.getpid(), current["created_at"] + 1))

    def test_intermediate_and_effective_processes_are_distinguished(self):
        own_executable = run_process._process_identity(os.getpid())["executable"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "intermediate.py"
            ready = root / "effective.pid"
            script.write_text(
                "import subprocess, time\n"
                f"service = subprocess.Popen([{own_executable!r}, '-c', 'import time; time.sleep(60)'])\n"
                f"with open({str(ready)!r}, 'w', encoding='ascii') as target:\n"
                "    target.write(str(service.pid))\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            launcher = subprocess.Popen(
                [own_executable, str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            service_pid = None
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and launcher.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "intermediate process did not publish its effective child PID")
                service_pid = int(ready.read_text(encoding="ascii"))
                launcher_identity = run_process._process_identity(launcher.pid)
                service_identity = run_process._process_identity(service_pid)
                self.assertIsNotNone(launcher_identity)
                self.assertIsNotNone(service_identity)
                self.assertNotEqual(launcher.pid, service_pid)
                self.assertEqual(
                    run_process._windows_process_created_at(launcher._handle), launcher_identity["created_at"]
                )

                with mock.patch.object(run_process, "RUN_ROOT", root / "run"):
                    run_process._write_process_record("web", "supervisor", os.getpid())
                    run_process._write_process_record(
                        "web", "launcher", launcher.pid, parent_pid=os.getpid()
                    )
                    parent_map = run_process._windows_parent_map()
                    run_process._write_process_record(
                        "web", "child", service_pid, parent_pid=parent_map[service_pid]
                    )
                    with mock.patch.object(run_process, "_is_locked", return_value=True):
                        with mock.patch("builtins.print") as output:
                            self.assertEqual(run_process._print_status("web"), 0)
                    rendered = " ".join(str(part) for call in output.call_args_list for part in call.args)
                    self.assertIn(f"launcher pid {launcher.pid}", rendered)
                    self.assertIn(f"child pid {service_pid}", rendered)
                    self.assertIn("running", rendered)

                    self.assertFalse(
                        run_process._terminate_windows_process(service_pid, service_identity["created_at"] + 1)
                    )
                    self.assertTrue(run_process._pid_is_running(service_pid, service_identity["created_at"]))
                    self.assertTrue(
                        run_process._terminate_windows_process(service_pid, service_identity["created_at"])
                    )
                    self.assertFalse(run_process._pid_is_running(service_pid, service_identity["created_at"]))
            finally:
                if service_pid is not None:
                    identity = run_process._process_identity(service_pid)
                    if identity:
                        run_process._terminate_windows_process(service_pid, identity["created_at"])
                if launcher.poll() is None:
                    launcher.terminate()
                    try:
                        launcher.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        launcher.kill()
                        launcher.wait(timeout=5)
                if launcher.stderr:
                    launcher.stderr.close()

    def test_saved_record_rejects_a_reused_pid(self):
        identity = run_process._process_identity(os.getpid())
        self.assertIsNotNone(identity)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(run_process, "RUN_ROOT", root):
                run_process._process_pid_path("web", "child").write_text(f"{os.getpid()}\n", encoding="ascii")
                run_process._process_record_path("web", "child").write_text(
                    json.dumps({"pid": os.getpid(), "created_at": identity["created_at"] + 1}),
                    encoding="utf-8",
                )
                self.assertIsNone(run_process._read_process_record("web", "child"))

    def test_child_entrypoint_records_the_effective_pid_and_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "invocation.json"
            script = root / "service.py"
            script.write_text(
                "import json, os, sys\n"
                f"with open({str(output)!r}, 'w', encoding='utf-8') as target:\n"
                "    target.write(json.dumps({'pid': os.getpid(), 'args': sys.argv[1:]}))\n",
                encoding="utf-8",
            )
            with mock.patch.object(run_process, "RUN_ROOT", root / "run"):
                with mock.patch.object(
                    run_process, "_command", return_value=[sys.executable, str(script), "integration_worker"]
                ):
                    self.assertEqual(run_process._run_managed_child("worker"), 0)
                invocation = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(invocation, {"pid": os.getpid(), "args": ["integration_worker"]})
                self.assertFalse(run_process._process_pid_path("worker", "child").exists())
                self.assertFalse(run_process._process_record_path("worker", "child").exists())


if __name__ == "__main__":
    unittest.main()
