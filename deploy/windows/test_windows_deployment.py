"""Focused update fences and rollback compatibility with the deployed 031e164 scripts."""
import contextlib
import io
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


class DeploymentFenceTests(unittest.TestCase):
    def test_snapshot_retry_retains_live_identity_after_old_pid_files_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "windows-manager-2-processes.json"
            path.write_text('[{"pid":123,"created_at":456}]')
            with mock.patch.object(run_process, "RUN_ROOT", root), \
                    mock.patch.object(run_process, "_read_process_record", return_value=None), \
                    mock.patch.object(run_process, "_pid_is_running", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_process._deployment_check("snapshot"), 0)
            self.assertEqual(json.loads(path.read_text()), [{"pid": 123, "created_at": 456}])

    def test_running_refuses_live_worker_with_a_lease_from_another_generation(self):
        created = int((time.time() - 10 + 11_644_473_600) * 10_000_000)
        records = {"supervisor": {"pid": 1, "created_at": created},
                   "launcher": {"pid": 2, "created_at": created},
                   "child": {"pid": 3, "created_at": created}}
        with mock.patch.object(run_process, "_setup_django"), \
                mock.patch.object(run_process, "_read_process_record", side_effect=lambda role, name, **kw: records[name]), \
                mock.patch.object(run_process, "_is_locked", return_value=True), \
                mock.patch.object(run_process, "_service_process_matches_launcher", return_value=True), \
                mock.patch.object(run_process, "_listener_pids", return_value={3}), \
                mock.patch.object(run_process, "_web_health", return_value=True), \
                mock.patch.object(run_process, "_deployment_worker_lease", return_value=False), \
                mock.patch.object(run_process.time, "monotonic", side_effect=[0, 31]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_process._deployment_check("running"), 1)
        self.assertIn("NOT complete", output.getvalue())

    def test_stopped_refuses_a_live_pid_even_after_records_and_lock_disappear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "windows-manager-2-processes.json").write_text('[{"pid":123,"created_at":456}]')
            with mock.patch.object(run_process, "RUN_ROOT", root), \
                    mock.patch.object(run_process, "_pid_is_running", return_value=True), \
                    mock.patch.object(run_process, "_is_locked", return_value=False), \
                    mock.patch.object(run_process, "_read_process_record", return_value=None), \
                    mock.patch.object(run_process, "_listener_pids", return_value=set()), \
                    mock.patch.object(run_process.time, "monotonic", side_effect=[0, 6]), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_process._deployment_check("stopped"), 1)
            self.assertIn("update refused", output.getvalue())

    def test_running_refuses_unavailable_services_without_claiming_completion(self):
        with mock.patch.object(run_process, "_setup_django"), \
                mock.patch.object(run_process, "_read_process_record", return_value=None), \
                mock.patch.object(run_process, "_is_locked", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_process._deployment_check("running"), 1)
        self.assertIn("NOT complete", output.getvalue())


@unittest.skipUnless(os.name == "nt" and os.getenv("ARION_WINDOWS_DEPLOY_DEV") == "1",
                     "Explicit opt-in to the isolated local MySQL rollback check")
class LegacyRollbackTests(unittest.TestCase):
    def test_real_legacy_stop_restart_and_new_fences(self):
        import test_windows_lifecycle as fixture_module

        # Reuse only the isolated fixture, never rerun its homologated test suite.
        class Fixture(fixture_module.RealWindowsLifecycleTests):
            @classmethod
            def call(cls, *args, **kwargs):
                cls.evidence = cls.repo / "var/windows-deployment-check"
                cls.evidence.mkdir(parents=True, exist_ok=True)
                return super().call(*args, **kwargs)

        Fixture.setUpClass()
        root, repo = Fixture.root, Fixture.repo
        helper = root / "var/run/windows-manager-2-lease.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_bytes((repo / "deploy/windows/run_process.py").read_bytes())
        files = ("run_process.py", "start-arion.ps1", "stop-arion.ps1", "status-arion.ps1", "manage-arion.ps1")
        originals = {name: (root / "deploy/windows" / name).read_bytes() for name in files}
        baseline = "031e164ed449776023f18c6392a117c8610223be"

        def helper_call(action, expected=0):
            return Fixture.call([Fixture.python, helper, "worker", "--environment", "development",
                                 "--deployment", action], expected=expected)

        def stop(role):
            Fixture.call(["powershell.exe", "-NoProfile", "-File", root / "deploy/windows/stop-arion.ps1",
                          "-Role", role, "-ProjectRoot", root])

        try:
            for name in files:
                content = subprocess.check_output(["git", "show", f"{baseline}:deploy/windows/{name}"], cwd=repo)
                (root / "deploy/windows" / name).write_bytes(content)
            legacy = root / "deploy/windows/legacy_run_process.py"
            legacy.write_bytes((root / "deploy/windows/run_process.py").read_bytes())
            # Old production-only supervisor, with DEV environment injection
            # solely in this temporary fixture. Process/stop logic is unchanged.
            (root / "deploy/windows/run_process.py").write_text(
                "import importlib.util, pathlib, os\n"
                "spec=importlib.util.spec_from_file_location('legacy',pathlib.Path(__file__).with_name('legacy_run_process.py'))\n"
                "r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);r.__file__=__file__\n"
                "original=r._child_environment\n"
                "def dev_environment():\n"
                "    env=original();env['ARION_ENV']='development';env['DJANGO_SETTINGS_MODULE']='windows_dev_settings';return env\n"
                "r._child_environment=dev_environment\nraise SystemExit(r.guarded_main())\n", encoding="utf-8")
            literal = str(root).replace("'", "''")
            bridge = root / "legacy-manager-test.ps1"
            bridge.write_text(
                f". '{literal}\\deploy\\windows\\manage-arion.ps1' -ProjectRoot '{literal}' -Action Start\n"
                "function Assert-AuthorizedOperator {} # Isolated DEV operator injection only.\n"
                "try { Start-Manager; exit 0 } catch { Write-Log $_.Exception.Message 'ERRO'; exit 1 } finally { Exit-OperationLock }\n",
                encoding="utf-8-sig")
            _, resolved = helper_call("root")
            self.assertEqual(Path(resolved.strip()).resolve(), root.resolve())
            Fixture.call(["powershell.exe", "-NoProfile", "-File", bridge])
            helper_call("running")
            helper_call("snapshot")
            stop("worker")
            stop("web")
            helper_call("stopped")
            # Legacy hard termination leaves its lease until natural expiry.
            # This is read-only: no lease deletion or reset is allowed.
            Fixture.call([Fixture.python, helper, "worker", "--environment", "development", "--wait-lease"], timeout=205)
            Fixture.call(["powershell.exe", "-NoProfile", "-File", bridge])
            Fixture.call(["powershell.exe", "-NoProfile", "-File", root / "deploy/windows/status-arion.ps1", "-ProjectRoot", root])
            helper_call("running")
            helper_call("snapshot")
            stop("worker")
            helper_call("running", expected=1)
            stop("web")
            helper_call("stopped")
            Fixture.django("from aplicativos.integracoes.models import IntegrationRun,OTRSTicket; assert not IntegrationRun.objects.exists() and not OTRSTicket.objects.exists(); print('No source jobs executed')")
        finally:
            for role in ("worker", "web"):
                stop(role)
            for name, content in originals.items():
                (root / "deploy/windows" / name).write_bytes(content)
            Fixture.tearDownClass()


if __name__ == "__main__":
    unittest.main()
