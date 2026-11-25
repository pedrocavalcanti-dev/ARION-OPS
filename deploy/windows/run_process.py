"""Windows process supervisor for the ARION web and integration worker tasks."""

from __future__ import annotations

import argparse
import _thread
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import runpy
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
from urllib.request import build_opener, ProxyHandler
from urllib.parse import urlsplit
import uuid

from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = PROJECT_ROOT / "var" / "run"
LOG_ROOT = PROJECT_ROOT / "var" / "logs"
ROLES = {"web", "worker"}
STOP_EVENT = threading.Event()
STABLE_SECONDS = 5
STARTUP_SECONDS = 30
MAX_RESTARTS = 5
RESTART_WINDOW = 300

SECRET_KEY_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret_key|secret|token|access_token|refresh_token|api_key|"
    r"client_secret|authorization|cookie|database_url|tunnel_token)\b"
    r"(\s*[:=]\s*|\s+)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
URL_PASSWORD_PATTERN = re.compile(r"(?i)(://[^:/@\s]+:)[^@/\s]+(@)")


def _sensitive_environment_values() -> list[str]:
    values = []
    for key, value in os.environ.items():
        if value and re.search(r"secret|password|token|credential|database_url|api_key|private_key", key, re.I):
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def sanitize_line(value: str) -> str:
    line = SECRET_KEY_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    line = URL_PASSWORD_PATTERN.sub(r"\1<redacted>\2", line)
    for secret in _sensitive_environment_values():
        line = line.replace(secret, "<redacted>")
    return line.rstrip("\r\n")[:4000]


def _logger(role: str) -> logging.Logger:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"arion.{role}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_ROOT / f"arion-{role}.log", maxBytes=10 * 1024 * 1024,
                                      backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _lock_file(role: str):
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    stream = (RUN_ROOT / f"{role}.lock").open("a+b")
    stream.seek(0)
    if not stream.read(1):
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        stream.close()
        return None
    return stream


def _is_locked(role: str) -> bool:
    path = RUN_ROOT / f"{role}.lock"
    if not path.exists():
        return False
    stream = path.open("a+b")
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return False
    except (OSError, BlockingIOError):
        return True
    finally:
        stream.close()


def _pid_path(role: str) -> Path:
    return RUN_ROOT / f"{role}.pid"


def _process_pid_path(role: str, process: str) -> Path:
    if process == "supervisor":
        return _pid_path(role)
    return RUN_ROOT / f"{role}.{process}.pid"


def _process_record_path(role: str, process: str) -> Path:
    return RUN_ROOT / f"{role}.{process}.json"


def _stop_path(role: str) -> Path:
    return RUN_ROOT / f"{role}.stop"


def _filetime_value(value) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _windows_process_created_at(handle) -> int | None:
    if os.name != "nt" or not handle:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None
    return _filetime_value(created)


def _windows_process_identity(pid: int) -> dict[str, int | str] | None:
    """Read process identity through kernel32, including its non-reusable start time."""
    if os.name != "nt" or pid <= 0:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(0x00100000 | 0x00001000, False, pid)
    if not handle:
        return None
    try:
        if kernel32.WaitForSingleObject(handle, 0) != 0x00000102:  # WAIT_TIMEOUT
            return None
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = wintypes.DWORD(len(image_buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image_buffer, ctypes.byref(image_length)):
            return None
        return {"pid": pid, "created_at": _filetime_value(created), "executable": image_buffer.value}
    finally:
        kernel32.CloseHandle(handle)


def _state_path(role: str) -> Path:
    return RUN_ROOT / f"{role}.state.json"


def _read_state(role: str) -> dict:
    try:
        value = json.loads(_state_path(role).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(role: str, state: str, **details) -> None:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    previous = _read_state(role)
    if (previous.get("state") == state and time.time() - previous.get("updated_at", 0) < 5
            and all(previous.get(key) == value for key, value in details.items())):
        return
    value = {"state": state, "updated_at": time.time(),
             "environment": os.environ.get("ARION_ENV", "production"), **details}
    path = _state_path(role)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _web_health() -> bool:
    # Never use inherited HTTP proxies or follow redirects for local readiness.
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(f"http://127.0.0.1:{_web_port()}/health/", timeout=2) as response:
            return response.status == 200 and json.loads(response.read(4096)).get("status") == "ok"
    except (OSError, ValueError, RuntimeError):
        return False


def _status(role: str) -> dict:
    saved = _read_state(role)
    supervisor = _read_process_record(role, "supervisor")
    launcher = _read_process_record(role, "launcher", require_running=False)
    service = _read_process_record(role, "child")
    service_matches = bool(supervisor and launcher and service
                           and _service_process_matches_launcher(launcher, service))
    ready = service_matches
    if role == "web" and service_matches:
        ready = int(service["pid"]) in _listener_pids(_web_port()) and _web_health()
    locked = _is_locked(role)
    state = saved.get("state", "starting")
    if not locked:
        # Keep a terminal failure visible until the operator acknowledges stop/start.
        state = "failed" if state == "failed" or service or (launcher and launcher["running"]) else "stopped"
    elif not supervisor:
        # Lock acquisition precedes publication of PID + creation time. This
        # interval is Starting, never a terminal failure or a healthy service.
        state = "starting"
    elif state == "running" and (not ready or saved.get("child_pid") != service["pid"]):
        state = "restarting"
    elif state not in {"running", "starting", "restarting", "failed"}:
        state = "starting"
    return {"role": role, "state": state, "locked": locked,
            "environment": saved.get("environment", "unknown"),
            "code": {"running": 0, "stopped": 1, "starting": 2, "restarting": 2, "failed": 3}[state],
            "supervisor_pid": supervisor["pid"] if supervisor else None,
            "launcher_pid": launcher["pid"] if launcher else None,
            "child_pid": service["pid"] if service else None,
            "last_exit_code": saved.get("last_exit_code"), "restarts": saved.get("restarts", 0),
            "detail": saved.get("detail", ""), "updated_at": saved.get("updated_at", 0),
            "checked_at": time.time()}


def _print_status(role: str, *, as_json: bool = False) -> int:
    status = _status(role)
    if as_json:
        print(json.dumps(status))
    else:
        print(f"{role}: {status['state']} (supervisor pid {status['supervisor_pid']}, "
              f"launcher pid {status['launcher_pid']}, child pid {status['child_pid']}; "
              f"restarts {status['restarts']}; exit {status['last_exit_code']})")
        if status["detail"]:
            print(sanitize_line(status["detail"]))
    return status["code"]


def _process_identity(pid: int) -> dict[str, int | str] | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_identity(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return {"pid": pid, "created_at": 0, "executable": ""}
    except OSError:
        return None
    return {"pid": pid, "created_at": 0, "executable": ""}


def _pid_is_running(pid: int, expected_created_at: int | None = None) -> bool:
    identity = _process_identity(pid)
    if identity is None:
        return False
    return expected_created_at is None or identity["created_at"] == expected_created_at


def _web_port() -> int:
    """Read the same safe, non-secret port setting used by Waitress startup."""
    value = os.environ.get("ARION_WEB_PORT")
    if value is None:
        try:
            value = dotenv_values(PROJECT_ROOT / ".env").get("ARION_WEB_PORT")
        except (OSError, UnicodeError):
            value = None
    try:
        port = int(value or "8000")
    except (TypeError, ValueError):
        raise RuntimeError("ARION_WEB_PORT inválida.") from None
    if not 1 <= port <= 65535:
        raise RuntimeError("ARION_WEB_PORT inválida.")
    return port


def _listener_pids(port: int, address: str = "127.0.0.1") -> set[int]:
    """Return Windows PIDs owning an IPv4 LISTEN socket at the exact endpoint."""
    if os.name != "nt":
        return set()

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD), ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL,
        wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
    )
    get_table.restype = wintypes.DWORD
    size = wintypes.ULONG(0)
    # AF_INET=2, TCP_TABLE_OWNER_PID_LISTENER=3, ERROR_INSUFFICIENT_BUFFER=122.
    result = get_table(None, ctypes.byref(size), False, 2, 3, 0)
    if result not in (0, 122) or size.value == 0:
        return set()
    buffer = ctypes.create_string_buffer(size.value)
    result = get_table(buffer, ctypes.byref(size), False, 2, 3, 0)
    if result != 0:
        return set()
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    row_offset = ctypes.sizeof(wintypes.DWORD)
    row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    expected_address = socket.inet_aton(address)
    matched = set()
    for index in range(count):
        row = MIB_TCPROW_OWNER_PID.from_buffer(buffer, row_offset + index * row_size)
        row_address = struct.pack("=I", row.dwLocalAddr)
        row_port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        if row.dwState == 2 and row_address == expected_address and row_port == port:
            matched.add(int(row.dwOwningPid))
    return matched


def _write_process_record(
    role: str,
    process: str,
    pid: int,
    *,
    parent_pid: int | None = None,
    executable: str | None = None,
    created_at: int | None = None,
) -> dict[str, int | str]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    identity = _process_identity(pid)
    if identity is None and created_at is None:
        raise RuntimeError(f"Não foi possível confirmar a identidade do processo {pid}.")
    record = {
        "pid": pid,
        "created_at": created_at if created_at is not None else identity["created_at"],
        "executable": executable or (identity["executable"] if identity else "") or sys.executable,
        "parent_pid": parent_pid,
    }
    pid_path = _process_pid_path(role, process)
    pid_temp = pid_path.with_name(f"{pid_path.name}.{uuid.uuid4().hex}.tmp")
    record_path = _process_record_path(role, process)
    record_temp = record_path.with_name(f"{record_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        pid_temp.write_text(f"{pid}\n", encoding="ascii")
        record_temp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(pid_temp, pid_path)
        os.replace(record_temp, record_path)
    finally:
        pid_temp.unlink(missing_ok=True)
        record_temp.unlink(missing_ok=True)
    return record


def _read_process_record(
    role: str, process: str, *, require_running: bool = True
) -> dict[str, int | str | bool] | None:
    try:
        pid = int(_process_pid_path(role, process).read_text(encoding="ascii").strip())
        record = json.loads(_process_record_path(role, process).read_text(encoding="utf-8"))
        created_at = int(record["created_at"])
        if int(record["pid"]) != pid:
            return None
        identity = _process_identity(pid)
        running = identity is not None and identity["created_at"] == created_at
        if require_running and not running:
            return None
        executable = identity["executable"] if running else record.get("executable", "")
        return {
            **record, "pid": pid, "created_at": created_at,
            "executable": executable, "running": running,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _windows_parent_map() -> dict[int, int]:
    if os.name != "nt":
        return {}

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        parents: dict[int, int] = {}
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        return parents
    finally:
        kernel32.CloseHandle(snapshot)


def _service_process_matches_launcher(
    launcher: dict[str, int | str | bool], service: dict[str, int | str | bool]
) -> bool:
    launcher_pid = int(launcher["pid"])
    service_pid = int(service["pid"])
    if service_pid == launcher_pid:
        return int(service["created_at"]) == int(launcher["created_at"])
    if int(service["created_at"]) < int(launcher["created_at"]):
        return False
    if os.name == "nt":
        parents = _windows_parent_map()
        if parents.get(service_pid) == launcher_pid:
            return True
        if service_pid not in parents and service.get("parent_pid") == launcher_pid:
            return True
        current = service_pid
        visited = set()
        for _ in range(128):
            parent = parents.get(current)
            if parent is None or parent == current or parent in visited:
                return False
            if parent == launcher_pid:
                return True
            visited.add(parent)
            current = parent
        return False
    return service.get("parent_pid") == launcher_pid


def _remove_process_record(role: str, process: str, *, only_pid: int | None = None) -> None:
    pid_path = _process_pid_path(role, process)
    if only_pid is not None:
        try:
            if int(pid_path.read_text(encoding="ascii").strip()) != only_pid:
                return
        except (OSError, ValueError):
            return
    pid_path.unlink(missing_ok=True)
    _process_record_path(role, process).unlink(missing_ok=True)


def _run_managed_child(role: str) -> int:
    """Run the service command in the effective interpreter and publish its PID."""
    target = _command(role)
    child_pid = os.getpid()
    _write_process_record(role, "child", child_pid, parent_pid=os.getppid())
    finished = threading.Event()

    def stop_monitor():
        while not finished.wait(0.2):
            if _stop_path(role).exists():
                # A Windows TerminateProcess bypasses Django's finally/release.
                # Deliver KeyboardInterrupt inside the effective interpreter.
                _thread.interrupt_main()
                return

    threading.Thread(target=stop_monitor, daemon=True).start()
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "arion_geo"))
        if target[1] == "-m":
            module_name = target[2]
            sys.argv = [module_name, *target[3:]]
            runpy.run_module(module_name, run_name="__main__", alter_sys=True)
        else:
            script_path = target[1]
            sys.argv = [script_path, *target[2:]]
            runpy.run_path(script_path, run_name="__main__")
    finally:
        finished.set()
        _remove_process_record(role, "child", only_pid=child_pid)
    return 0


def _load_project_environment(project_root: Path | None = None) -> dict[str, str | bool]:
    """Load the fixed project .env using OS environment precedence and report sources safely."""
    dotenv_path = (project_root or PROJECT_ROOT) / ".env"
    inherited_keys = set(os.environ)
    try:
        file_values = dotenv_values(dotenv_path)
    except (OSError, UnicodeError):
        file_values = {}
    load_dotenv(dotenv_path, override=False)
    key = "ARION_OTRS_LEGACY_NO_TLS_HOST"
    if key in inherited_keys:
        source = "process_environment"
    elif str(file_values.get(key) or "").strip():
        source = "project_dotenv"
    else:
        source = "unset"
    return {"dotenv_present": dotenv_path.is_file(), "otrs_legacy_host_source": source}


def _child_environment() -> dict[str, str]:
    """Build the same production environment for the web and worker children."""
    environment = os.environ.copy()
    environment["ARION_ENV"] = os.environ.get("ARION_ENV", "production")
    environment["DJANGO_SETTINGS_MODULE"] = (os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings")
                                              if environment["ARION_ENV"] == "development" else "config.settings")
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _request_stop(role: str) -> int:
    if not _is_locked(role):
        if not _cleanup_orphan(role):
            print(f"{role}: failed; recorded process could not be stopped safely")
            return 3
        _stop_path(role).unlink(missing_ok=True)
        _write_state(role, "stopped")
        print(f"{role}: stopped")
        return 0
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    _logger(role).info("stop requested through control command requester_pid=%s", os.getpid())
    _stop_path(role).write_text("stop\n", encoding="ascii")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not _is_locked(role):
            if _read_process_record(role, "child") or _read_process_record(role, "launcher"):
                print(f"{role}: failed; effective process remains alive")
                return 3
            print(f"{role}: stopped")
            return 0
        time.sleep(0.5)
    print(f"{role}: stop requested; supervisor has not exited yet")
    return 2


def _command(role: str) -> list[str]:
    python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError("O interpretador da .venv não foi encontrado.")
    if role == "web":
        port = _web_port()
        return [
            str(python),
            "-m",
            "waitress",
            "--listen=127.0.0.1:" + str(port),
            "--trusted-proxy=127.0.0.1",
            "--trusted-proxy-count=1",
            "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto x-forwarded-host",
            "--clear-untrusted-proxy-headers",
            "config.wsgi:application",
        ]
    return [str(python), str(PROJECT_ROOT / "arion_geo" / "gerenciar.py"), "integration_worker"]


def _read_child_output(child: subprocess.Popen, logger: logging.Logger, ready: threading.Event) -> None:
    if child.stdout is None:
        return
    for line in child.stdout:
        logger.info("child: %s", sanitize_line(line))
        if line.strip() == "Worker iniciado.":
            ready.set()


def _terminate_windows_process(pid: int, expected_created_at: int) -> bool:
    """Terminate only the exact process recorded by the service child."""
    if os.name != "nt" or pid <= 0:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    # PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(0x00000001 | 0x00100000 | 0x00001000, False, pid)
    if not handle:
        return False
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ) or _filetime_value(created) != expected_created_at:
            return False
        if kernel32.WaitForSingleObject(handle, 0) != 0x00000102:
            return True
        if not kernel32.TerminateProcess(handle, 1):
            return False
        return kernel32.WaitForSingleObject(handle, 10000) == 0
    finally:
        kernel32.CloseHandle(handle)


def _cleanup_orphan(role: str) -> bool:
    """Never discard a live effective child after a crashed supervisor."""
    launcher = _read_process_record(role, "launcher", require_running=False)
    service = _read_process_record(role, "child")
    if service:
        if not launcher or not _service_process_matches_launcher(launcher, service):
            return False
        _stop_path(role).write_text("stop\n", encoding="ascii")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _pid_is_running(int(service["pid"]), int(service["created_at"])):
            time.sleep(0.2)
        if (_pid_is_running(int(service["pid"]), int(service["created_at"]))
                and not _terminate_windows_process(int(service["pid"]), int(service["created_at"]))):
            return False
    if launcher and launcher["running"]:
        supervisor = _read_process_record(role, "supervisor", require_running=False)
        if (not supervisor or launcher.get("parent_pid") != supervisor["pid"]
                or int(launcher["created_at"]) < int(supervisor["created_at"])):
            return False
        if (_pid_is_running(int(launcher["pid"]), int(launcher["created_at"]))
                and not _terminate_windows_process(int(launcher["pid"]), int(launcher["created_at"]))):
            return False
    for process in ("child", "launcher", "supervisor"):
        _remove_process_record(role, process)
    return True


def _stop_child(child: subprocess.Popen, logger: logging.Logger, role: str) -> bool:
    service = None
    owned_service = False
    try:
        service = json.loads(_process_record_path(role, "child").read_text(encoding="utf-8"))
        launcher = json.loads(_process_record_path(role, "launcher").read_text(encoding="utf-8"))
        if int(launcher["pid"]) == child.pid and int(service["pid"]) > 0:
            owned_service = _service_process_matches_launcher(launcher, service)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        service = None

    logger.info("requesting graceful shutdown of effective child")
    _stop_path(role).write_text("stop\n", encoding="ascii")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        effective_alive = bool(owned_service and service and
                               _pid_is_running(int(service["pid"]), int(service["created_at"])))
        if child.poll() is not None and not effective_alive:
            break
        time.sleep(0.2)
    if child.poll() is None:
        logger.warning("graceful shutdown timed out; terminating recorded launcher")
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            logger.warning("child did not stop in time; terminating process")
            child.kill()
            child.wait(timeout=10)

    # A Microsoft Store launcher can leave the effective interpreter alive after
    # the .venv\Scripts\python.exe intermediary exits. The saved parent chain
    # and creation time restrict cleanup to the exact service process.
    if owned_service and service:
        try:
            service_pid = int(service["pid"])
            if service_pid != child.pid and _pid_is_running(service_pid, int(service["created_at"])):
                if not _terminate_windows_process(service_pid, int(service["created_at"])):
                    logger.warning("could not stop the recorded effective child process %s", service_pid)
                    return False
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
    return child.poll() is not None and not _read_process_record(role, "child")


def supervise(role: str) -> int:
    logger = _logger(role)
    lock = _lock_file(role)
    if lock is None:
        status = _status(role)
        logger.warning("duplicate start ignored; existing state=%s", status["state"])
        return status["code"]
    child = None
    final_state = "stopped"
    restarts = []
    STOP_EVENT.clear()
    stop_file = _stop_path(role)
    try:
        if not _cleanup_orphan(role):
            final_state = "failed"
            _write_state(role, final_state, detail="Existing recorded child cannot be safely replaced; run stop and inspect logs.")
            return 1
        stop_file.unlink(missing_ok=True)
        _write_process_record(role, "supervisor", os.getpid(), parent_pid=os.getppid())
        _write_state(role, "starting", supervisor_pid=os.getpid())
        signal.signal(signal.SIGTERM, lambda *_: STOP_EVENT.set())
        signal.signal(signal.SIGINT, lambda *_: STOP_EVENT.set())
        environment_summary = _load_project_environment()
        logger.info("supervisor started role=%s environment=%s dotenv_present=%s",
                    role, os.environ["ARION_ENV"], environment_summary["dotenv_present"])
        while not STOP_EVENT.is_set():
            if stop_file.exists():
                STOP_EVENT.set()
                break
            _write_state(role, "restarting" if restarts else "starting", restarts=len(restarts))
            target = _command(role)
            ready = threading.Event()
            child = subprocess.Popen(
                [target[0], str(Path(__file__).resolve()), "--run-child", role],
                cwd=PROJECT_ROOT / "arion_geo", env=_child_environment(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _write_process_record(role, "launcher", child.pid, parent_pid=os.getpid(),
                created_at=_windows_process_created_at(child._handle) if os.name == "nt" else None)
            logger.info("child launcher started pid=%s attempt=%s", child.pid, len(restarts) + 1)
            reader = threading.Thread(target=_read_child_output, args=(child, logger, ready), daemon=True)
            reader.start()
            started = time.monotonic()
            ready_since = None
            unavailable_since = started
            ever_running = False
            next_health = 0
            web_healthy = False
            reason = "Child exited unexpectedly (including exit 0 for a persistent service)."
            while not STOP_EVENT.wait(0.2):
                if stop_file.exists():
                    STOP_EVENT.set()
                    break
                launcher = _read_process_record(role, "launcher", require_running=False)
                service = _read_process_record(role, "child")
                matches = bool(launcher and service and _service_process_matches_launcher(launcher, service))
                alive = child.poll() is None or matches
                # The Store launcher may exit while the effective interpreter
                # still owns the output pipe and is publishing its identity.
                if not alive and not reader.is_alive():
                    break
                now = time.monotonic()
                if role == "web" and matches and now >= next_health:
                    web_healthy = int(service["pid"]) in _listener_pids(_web_port()) and _web_health()
                    next_health = now + 5
                healthy = matches and (ready.is_set() if role == "worker" else web_healthy)
                if healthy:
                    ready_since = ready_since or now
                    unavailable_since = now
                    if now - ready_since >= STABLE_SECONDS:
                        ever_running = True
                        _write_state(role, "running", child_pid=service["pid"], restarts=len(restarts))
                        if now - ready_since >= RESTART_WINDOW:
                            restarts.clear()
                else:
                    ready_since = None
                    if ever_running:
                        _write_state(role, "restarting", restarts=len(restarts), detail="Effective child lost readiness.")
                if not ever_running and now - started >= STARTUP_SECONDS:
                    reason = "Startup not confirmed: check worker lease, application error and startup logs."
                    break
                if ever_running and now - unavailable_since >= STARTUP_SECONDS:
                    reason = "Effective child lost readiness for the startup budget."
                    break
            code = child.poll()
            if not _stop_child(child, logger, role):
                final_state = "failed"
                _write_state(role, final_state, detail="Recorded child did not stop; duplicate launch refused.")
                return 1
            reader.join(timeout=2)
            if child.stdout and not reader.is_alive():
                child.stdout.close()
            child = None
            for process in ("child", "launcher"):
                _remove_process_record(role, process)
            if STOP_EVENT.is_set():
                break
            stop_file.unlink(missing_ok=True)
            now = time.monotonic()
            restarts = [stamp for stamp in restarts if now - stamp < RESTART_WINDOW]
            restarts.append(now)
            terminal = len(restarts) >= MAX_RESTARTS
            delay = min(2 ** len(restarts), 30)
            final_state = "failed" if terminal else "stopped"
            _write_state(role, "failed" if terminal else "restarting", restarts=len(restarts),
                         last_exit_code=code, detail=reason)
            logger.error("child unavailable exit=%s failures=%s/%s; %s", code, len(restarts), MAX_RESTARTS, reason)
            if terminal:
                return 1
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not STOP_EVENT.wait(0.2):
                if stop_file.exists():
                    STOP_EVENT.set()
    except Exception:
        final_state = "failed"
        _write_state(role, final_state, detail="Supervisor exception; inspect sanitized role log.")
        logger.error("supervisor failed: %s", sanitize_line(traceback.format_exc()))
        return 1
    finally:
        cleaned = child is None or _stop_child(child, logger, role)
        if cleaned:
            for process in ("child", "launcher", "supervisor"):
                _remove_process_record(role, process)
        else:
            final_state = "failed"
            _write_state(role, final_state, detail="Cleanup incomplete; identities retained for safe stop.")
        stop_file.unlink(missing_ok=True)
        if final_state != "failed":
            _write_state(role, "stopped")
        logger.info("supervisor stopped state=%s", final_state)
        lock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARION Windows process supervisor")
    parser.add_argument("--run-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("role", type=str.casefold, choices=sorted(ROLES))
    parser.add_argument("--json", action="store_true", help="Machine-readable status")
    parser.add_argument("--environment", type=str.casefold, choices=("production", "development"), default="production")
    parser.add_argument("--wait-for-web", action="store_true", help="Boot worker only after managed Web readiness")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--stop", action="store_true")
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--wait-lease", action="store_true", help="Read-only wait for a former worker lease to expire")
    action.add_argument("--deployment", choices=("root", "snapshot", "stopped", "running"),
                        help="Deployment fences compatible with previous Windows scripts")
    args = parser.parse_args()
    if not args.run_child:
        os.environ["ARION_ENV"] = args.environment
    if args.run_child:
        return _run_managed_child(args.role)
    if args.status:
        return _print_status(args.role, as_json=args.json)
    if args.stop:
        return _request_stop(args.role)
    if args.preflight:
        return _preflight(args.role)
    if args.wait_lease:
        return _wait_worker_lease()
    if args.deployment:
        return _deployment_check(args.deployment)
    if args.wait_for_web and args.role == "worker":
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            if _status("web")["state"] == "running":
                break
            time.sleep(1)
        else:
            _write_state("worker", "failed", detail="Boot refused: managed Web is not ready.")
            _logger("worker").error("boot refused: managed Web is not ready")
            return 1
    return supervise(args.role)


def _setup_django():
    sys.path.insert(0, str(PROJECT_ROOT / "arion_geo"))
    _load_project_environment()
    os.environ.update(_child_environment())
    import django
    django.setup()


def _worker_lease_seconds() -> float:
    from aplicativos.integracoes.models import IntegrationLease
    from django.utils import timezone
    row = IntegrationLease.objects.filter(key="worker").values("expires_at", "owner_token").first()
    return max(0, (row["expires_at"] - timezone.now()).total_seconds()) if row and row["owner_token"] else 0


def _wait_worker_lease() -> int:
    _setup_django()
    deadline = time.monotonic() + 190
    while _worker_lease_seconds() > 0:
        if time.monotonic() >= deadline:
            print("Worker lease remains active; another worker may own it. Nothing was changed.")
            return 1
        time.sleep(1)
    print("Worker lease available; no database record was modified.")
    return 0


def _deployment_check(action: str) -> int:
    """Verify physical processes across versions, without changing services/DB."""
    snapshot_path = RUN_ROOT / "windows-manager-2-processes.json"
    if action == "root":
        print(PROJECT_ROOT)
        return 0
    if action == "snapshot":
        records = [record for role in sorted(ROLES) for name in ("supervisor", "launcher", "child")
                   if (record := _read_process_record(role, name, require_running=False))]
        if snapshot_path.exists():
            # A failed old stop can delete its PID files before the effective
            # child exits. Retrying/rolling back must retain that live identity.
            previous = json.loads(snapshot_path.read_text(encoding="utf-8"))
            records.extend(row for row in previous if _pid_is_running(int(row["pid"]), int(row["created_at"])))
        records = list({(row["pid"], row["created_at"]): row for row in records}.values())
        RUN_ROOT.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(records), encoding="utf-8")
        print("Pre-stop identities captured (PID and creation time).")
        return 0
    if action == "stopped":
        records = json.loads(snapshot_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while True:
            alive = any(_pid_is_running(int(row["pid"]), int(row["created_at"])) for row in records)
            alive = alive or any(_is_locked(role) or any(_read_process_record(role, name)
                for name in ("supervisor", "launcher", "child")) for role in ROLES)
            if not alive and not _listener_pids(_web_port()):
                print("Web and Worker stopped: recorded processes exited; local Web listener released.")
                return 0
            if time.monotonic() >= deadline:
                print("Stop NOT confirmed; code update refused. Inspect recorded PIDs and role logs.")
                return 1
            time.sleep(0.2)
    _setup_django()
    deadline = time.monotonic() + STARTUP_SECONDS
    previous = None
    confirmations = 0
    while True:
        identities = []
        healthy = True
        for role in sorted(ROLES):
            supervisor = _read_process_record(role, "supervisor")
            launcher = _read_process_record(role, "launcher", require_running=False)
            service = _read_process_record(role, "child")
            if not (supervisor and launcher and service and _is_locked(role)
                    and _service_process_matches_launcher(launcher, service)):
                healthy = False
                break
            created = int(service["created_at"]) / 10_000_000 - 11_644_473_600 if os.name == "nt" else 0
            if time.time() - created < STABLE_SECONDS:
                healthy = False
            identities.append((supervisor["pid"], supervisor["created_at"], service["pid"], service["created_at"]))
            if role == "web":
                healthy = healthy and int(service["pid"]) in _listener_pids(_web_port()) and _web_health()
            else:
                healthy = healthy and _deployment_worker_lease(created)
        confirmations = confirmations + 1 if healthy and identities == previous else (1 if healthy else 0)
        previous = identities
        if confirmations >= 3:
            print("Web and Worker confirmed: stable effective PIDs, HTTP health and current worker lease.")
            return 0
        if not all(_is_locked(role) for role in ROLES) or time.monotonic() >= deadline:
            print("Web/Worker availability NOT confirmed; deployment is NOT complete.")
            return 1
        time.sleep(1)


def _deployment_worker_lease(child_created: float) -> bool:
    from aplicativos.integracoes.models import IntegrationLease
    from django.utils import timezone
    row = IntegrationLease.objects.filter(key="worker").values(
        "owner_token", "started_at", "heartbeat_at", "expires_at").first()
    now = timezone.now()
    return bool(row and row["owner_token"] and row["started_at"] and row["heartbeat_at"]
                and row["started_at"].timestamp() >= child_created - 1
                and row["expires_at"] > now and 0 <= (now - row["heartbeat_at"]).total_seconds() <= 60)


def _preflight(role: str = "web") -> int:
    """Read-only dependency/settings/DB check; never run migrations or jobs."""
    try:
        import importlib.util
        assert importlib.util.find_spec("waitress") is not None, "Waitress missing"
        assert sys.version_info[:2] == (3, 12), "Python 3.12 required"
        _setup_django()
        from django.conf import settings
        from django.db import connection
        if os.environ["ARION_ENV"] == "production":
            assert settings.DEBUG is False, "DEBUG must be False"
            assert connection.vendor == "mysql", "Production requires MySQL"
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1
        _command("web")
        if role == "worker" and _worker_lease_seconds() > 0:
            print("Worker lease is active; stop its owner or wait for expiry. No lease was modified.")
            raise RuntimeError("Worker lease is still active. Stop the owner or wait for expiry; never clear the lease manually.")
        print("Environment, Python 3.12, Django, Waitress and database: OK")
        return 0
    except Exception as error:
        print(f"Preflight failed ({type(error).__name__}); inspect sanitized startup log.")
        _logger(role).error("preflight failed: %s", sanitize_line(traceback.format_exc()))
        return 1


def guarded_main() -> int:
    """Record failures before the normal supervisor loop/child logger starts."""
    # runpy replaces sys.argv with the application's arguments. Retain the
    # management role before entering it so child failures reach the right log.
    role = next((value.casefold() for value in sys.argv[1:] if value.casefold() in ROLES), "startup")
    managed_child = "--run-child" in sys.argv
    try:
        return main()
    except Exception:
        diagnostic = sanitize_line(traceback.format_exc())
        if managed_child:
            # Single rotating-log writer: the supervisor captures this stream.
            print(diagnostic, file=sys.stderr, flush=True)
        else:
            _logger(role).error("supervisor startup failed: %s", diagnostic)
        return 1


if __name__ == "__main__":
    raise SystemExit(guarded_main())
