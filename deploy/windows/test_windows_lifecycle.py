"""Opt-in real Waitress/Django worker lifecycle, with an isolated local MySQL.

No migrations, source synchronizations or application test suites are run.
The temporary manager bridge replaces only the Administrator check so that an
unelevated DEV runner can exercise orchestration; the real guard is also tested.
"""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "nt" and os.getenv("ARION_WINDOWS_MYSQL_DEV") == "1",
                     "Requires explicit opt-in and the disposable local MySQL fixture")
class RealWindowsLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[2]
        cls.scripts = cls.repo / "deploy/windows"
        cls.root = Path(tempfile.mkdtemp(prefix="arion-real-windows-dev-")).resolve()
        cls.evidence = cls.repo / "var/windows-homologation"
        cls.evidence.mkdir(parents=True, exist_ok=True)
        cls.sequence = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.env = {k: v for k, v in os.environ.items() if not re.search(
            r"(?i)^(ARION|DJANGO|OTRS|CMDB|DATABASE|PYTHONPATH)|password|secret|token|api_key", k)}
        cls.env.update(ARION_ENV="development", DJANGO_SETTINGS_MODULE="windows_dev_settings",
                       DJANGO_DEBUG="false", DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,testserver",
                       ARION_WEB_PORT=str(cls.port), PYTHONIOENCODING="utf-8",
                       PYTHONPATH=str(cls.root / "arion_geo"), ARION_MANAGER_NO_PAUSE="1")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(cls.root / ".venv")],
                       check=True, capture_output=True, timeout=60)
        cls.python = cls.root / ".venv/Scripts/python.exe"
        import dotenv
        packages = Path(dotenv.__file__).resolve().parent.parent
        (cls.root / ".venv/Lib/site-packages/arion-dev.pth").write_text(str(packages), encoding="utf-8")
        for source in (cls.repo / "arion_geo").rglob("*.py"):
            if "__pycache__" in source.parts:
                continue
            target = cls.root / source.relative_to(cls.repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copytree(cls.scripts, cls.root / "deploy/windows", ignore=shutil.ignore_patterns("__pycache__"))
        releases = cls.root / "docs/08-releases"
        releases.mkdir(parents=True)
        shutil.copy2(cls.repo / "docs/08-releases/releases.json", releases / "releases.json")
        (cls.root / ".git").write_text("gitdir: " + (cls.repo / ".git").as_posix(), encoding="utf-8")
        (cls.root / ".env").write_text("ARION_ENV=development\nDJANGO_DEBUG=false\n", encoding="utf-8")
        cfg_path = cls.repo / "var/mysql-import-validation/connection.json"
        cfg = json.loads(cfg_path.read_text())
        if str(cfg.get("host", "")).lower() not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Fixture must use loopback MySQL")
        cls.settings = cls.root / "arion_geo/windows_dev_settings.py"
        cls.settings.write_text(
            "from config.settings import *\nimport pymysql\npymysql.install_as_MySQLdb()\n"
            + "DATABASES={'default':" + repr({"ENGINE": "django.db.backends.mysql", "NAME": "arion_geo_bootstrap",
                **{k.upper(): v for k, v in cfg.items()}, "OPTIONS": {"charset": "utf8mb4"}}) + "}\n",
            encoding="utf-8")
        cls.bridge = cls.root / "manager-dev-test.ps1"
        literal = str(cls.root).replace("'", "''")
        cls.bridge.write_text(
            "param([string]$Action='Status',[string]$Role='all')\n"
            f". '{literal}\\deploy\\windows\\manage-arion.ps1' -ProjectRoot '{literal}' -Action $Action -Role $Role -Environment development\n"
            "# Test-only operator injection; production files retain the real guard.\n"
            f"function Assert-AuthorizedOperator {{ if ($script:Root -ne '{literal}') {{ throw 'Wrong DEV root' }} }}\n"
            "try { Start-Manager; exit 0 } catch { Write-Log $_.Exception.Message 'ERRO'; exit 1 } finally { Exit-OperationLock }\n",
            encoding="utf-8-sig")
        # Existing disposable schema only. Never migrate or seed a live mirror.
        cls.django("""
from datetime import timedelta
from django.utils import timezone
from django.db import connection
from aplicativos.integracoes.models import (OTRSTicket, IntegrationRun, Integration, IntegrationLease,
    IntegrationSyncState, ExternalAPIIntegration, WeatherCollectionJob)
from aplicativos.cancelamentos.models import CancellationProcess, CancellationIntake, IntakeSettings
assert connection.settings_dict['NAME']=='arion_geo_bootstrap'
assert not any(model.objects.exists() for model in (OTRSTicket,IntegrationRun,CancellationProcess,CancellationIntake,WeatherCollectionJob))
assert not IntegrationLease.objects.filter(expires_at__gt=timezone.now(),owner_token__gt='').exists()
IntegrationSyncState.objects.update_or_create(key='cmdb',defaults={'last_success_at':timezone.now()+timedelta(days=1)})
Integration.objects.update_or_create(key='otrs',defaults={'enabled':False})
IntakeSettings.objects.get_or_create(defaults={'enabled':False})
from aplicativos.integracoes.services.geo_configuration import bootstrap_geo_integrations
bootstrap_geo_integrations()
ExternalAPIIntegration.objects.all().update(enabled=False)
print('Isolated MySQL fixture ready; external jobs disabled')
""")

    @classmethod
    def call(cls, args, *, shell=None, expected=0, input=None, timeout=80):
        label = f"{len(cls.sequence)+1:02d}"
        output = cls.evidence / f"{label}-call.log"
        with output.open("wb") as stream:
            result = subprocess.run([str(a) for a in args], cwd=cls.root, env=cls.env,
                input=input.encode() if input is not None else None, stdout=stream, stderr=subprocess.STDOUT,
                timeout=timeout)
        content = output.read_text(encoding="utf-8-sig", errors="replace")
        cls.sequence.append({"step": label, "command": [Path(str(a)).name for a in args[:2]], "exit": result.returncode})
        if expected is not None and result.returncode != expected:
            raise AssertionError(f"Exit {result.returncode}, expected {expected}: {content[-4000:]}")
        return result.returncode, content

    @classmethod
    def django(cls, code):
        return cls.call([cls.python, "arion_geo/gerenciar.py", "shell", "-c", code])

    @classmethod
    def manage(cls, action, role="all", *, host="powershell.exe", expected=0):
        return cls.call([host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cls.bridge,
                         "-Action", action, "-Role", role], expected=expected)

    @classmethod
    def status(cls, role):
        return json.loads(cls.call([cls.python, "deploy/windows/run_process.py", "--status", role, "--json"],
                                  expected=None)[1])

    def test_real_services_terminal_persistence_partial_restarts_and_recovery(self):
        # Exercise the actual BAT/menu, including reopening the caller terminal.
        bat = self.root / "deploy/windows/ARION-OPS.bat"
        self.call(["cmd.exe", "/d", "/c", bat, "-Environment", "development"], input="0\n")
        self.manage("Start")
        first = {role: self.status(role) for role in ("web", "worker")}
        self.assertTrue(all(item["state"] == "running" for item in first.values()))
        self.manage("Start")
        self.assertEqual({r: self.status(r)["child_pid"] for r in first}, {r: first[r]["child_pid"] for r in first})
        # Every call already ended its PowerShell process. Services must survive.
        self.call(["cmd.exe", "/d", "/c", bat, "-Environment", "development"], input="10\n\n0\n")
        for _ in range(3):
            for role in first:
                self.assertEqual(self.status(role)["state"], "running")
            time.sleep(1)
        self.manage("Restart", "web")
        self.assertEqual(self.status("worker")["child_pid"], first["worker"]["child_pid"])
        self.assertNotEqual(self.status("web")["child_pid"], first["web"]["child_pid"])
        web_pid = self.status("web")["child_pid"]
        self.manage("Restart", "worker")
        self.assertEqual(self.status("web")["child_pid"], web_pid)
        self.manage("Restart")
        self.manage("Health")
        self.manage("WebLogs")
        self.manage("WorkerLogs")
        self.manage("Stop")
        self.manage("Stop")
        self.django("from aplicativos.integracoes.models import IntegrationLease; from django.utils import timezone; row=IntegrationLease.objects.get(key='worker'); assert not row.owner_token and row.expires_at<=timezone.now(); print('Graceful shutdown released worker lease')")
        self.manage("Start")
        # Real preflight failure must leave the healthy Web untouched.
        self.manage("Stop", "worker")
        web_pid = self.status("web")["child_pid"]
        settings = self.settings.read_text()
        self.settings.write_text(settings + "\nraise RuntimeError('password=dev-secret-never-print')\n", encoding="utf-8")
        try:
            _, output = self.manage("Start", "worker", expected=1)
            self.assertNotIn("dev-secret-never-print", output)
            self.assertEqual(self.status("web")["child_pid"], web_pid)
        finally:
            self.settings.write_text(settings, encoding="utf-8")
        self.manage("Start", "worker")
        # Unmodified CLI guard denies this unelevated DEV token.
        self.call(["powershell.exe", "-NoProfile", "-File", self.root / "deploy/windows/manage-arion.ps1",
                   "-Action", "Stop", "-ProjectRoot", self.root], expected=1)
        self.assertEqual(self.status("web")["state"], "running")
        pwsh = self.repo / "var/windows-tools/pwsh/pwsh.exe"
        if pwsh.exists():
            self.manage("Restart", "worker", host=str(pwsh))
            self.manage("Health", host=str(pwsh))
        self.manage("Stop")
        # Individual scripts work with the same service identities and protocol.
        for role in ("web", "worker"):
            for name in ("start-arion.ps1", "restart-arion.ps1"):
                self.call(["powershell.exe", "-NoProfile", "-File", self.root / "deploy/windows" / name,
                           "-Role", role, "-ProjectRoot", self.root, "-Environment", "development"])
        self.call(["powershell.exe", "-NoProfile", "-File", self.root / "deploy/windows/status-arion.ps1", "-ProjectRoot", self.root])
        self.django("from aplicativos.integracoes.models import IntegrationRun, OTRSTicket; assert not IntegrationRun.objects.exists() and not OTRSTicket.objects.exists(); print('No CMDB/OTRS import or source synchronization executed')")

    @classmethod
    def tearDownClass(cls):
        try:
            for role in ("worker", "web"):
                cls.call(["powershell.exe", "-NoProfile", "-File", cls.root / "deploy/windows/stop-arion.ps1",
                          "-Role", role, "-ProjectRoot", cls.root])
            for source in (cls.root / "var/logs").glob("arion-*.log"):
                shutil.copy2(source, cls.evidence / source.name)
            (cls.evidence / "result.json").write_text(json.dumps({"steps": cls.sequence, "port": cls.port,
                "admin_bridge": True, "root": str(cls.root)}, indent=2), encoding="utf-8")
        finally:
            # Leave the disposable DEV root available for inspecting sanitized logs.
            print(f"DEV evidence: {cls.evidence}")


if __name__ == "__main__":
    unittest.main()
