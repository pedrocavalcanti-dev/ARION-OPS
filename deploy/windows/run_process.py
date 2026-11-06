"""Windows process supervisor for the ARION web and integration worker tasks."""

from __future__ import annotations

import argparse
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
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = PROJECT_ROOT / "var" / "run"
LOG_ROOT = PROJECT_ROOT / "var" / "logs"
ROLES = {"web", "worker"}
STOP_EVENT = threading.Event()

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


def _print_status(role: str) -> int:
    if not _is_locked(role):
        print(f"{role}: stopped")
        return 1
    supervisor = _read_process_record(role, "supervisor")
    launcher = _read_process_record(role, "launcher", require_running=False)
    service = _read_process_record(role, "child")
    supervisor_pid = str(supervisor["pid"]) if supervisor else "unknown"
    if (supervisor and launcher and service
            and _service_process_matches_launcher(launcher, service)):
        print(
            f"{role}: running (supervisor pid {supervisor_pid}, "
            f"launcher pid {launcher['pid']} ({'running' if launcher['running'] else 'exited'}), "
            f"child pid {service['pid']}, "
            f"executable {service['executable']})"
        )
        return 0
    launcher_pid = str(launcher["pid"]) if launcher else "unknown"
    child_pid = str(service["pid"]) if service else "unknown"
    print(
        f"{role}: supervisor running (pid {supervisor_pid}); "
        f"launcher pid {launcher_pid}, child pid {child_pid}; child restarting or unavailable"
    )
    return 2


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
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "arion_geo"))
        if target[1] == "-m":
            module_name = target[2]
            sys.argv = ["-m", module_name, *target[3:]]
            runpy.run_module(module_name, run_name="__main__", alter_sys=True)
        else:
            script_path = target[1]
            sys.argv = [script_path, *target[2:]]
            runpy.run_path(script_path, run_name="__main__")
    finally:
        _remove_process_record(role, "child", only_pid=child_pid)
    return 0


def _load_project_environment(project_root: Path = PROJECT_ROOT) -> dict[str, str | bool]:
    """Load the fixed project .env using OS environment precedence and report sources safely."""
    dotenv_path = project_root / ".env"
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
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
    environment["ARION_ENV"] = "production"
    return environment


def _request_stop(role: str) -> int:
    if not _is_locked(role):
        print(f"{role}: stopped")
        return 0
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    _stop_path(role).write_text("stop\n", encoding="ascii")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not _is_locked(role):
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
        try:
            port = int(os.environ.get("ARION_WEB_PORT", "8000"))
        except ValueError:
            raise RuntimeError("ARION_WEB_PORT inválida.") from None
        if not 1 <= port <= 65535:
            raise RuntimeError("ARION_WEB_PORT inválida.")
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


def _read_child_output(child: subprocess.Popen, logger: logging.Logger) -> None:
    if child.stdout is None:
        return
    for line in child.stdout:
        logger.info("child: %s", sanitize_line(line))


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


def _stop_child(child: subprocess.Popen, logger: logging.Logger, role: str) -> None:
    service = None
    owned_service = False
    try:
        service = json.loads(_process_record_path(role, "child").read_text(encoding="utf-8"))
        launcher = json.loads(_process_record_path(role, "launcher").read_text(encoding="utf-8"))
        if int(launcher["pid"]) == child.pid and int(service["pid"]) > 0:
            owned_service = _service_process_matches_launcher(launcher, service)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        service = None

    if child.poll() is not None:
        pass
    else:
        logger.info("requesting child shutdown")
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
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass


def supervise(role: str) -> int:
    logger = _logger(role)
    lock = _lock_file(role)
    if lock is None:
        logger.warning("duplicate start ignored")
        return 0

    child = None
    stop_file = _stop_path(role)
    stop_file.unlink(missing_ok=True)
    for process in ("supervisor", "launcher", "child"):
        _remove_process_record(role, process)
    _write_process_record(role, "supervisor", os.getpid(), parent_pid=os.getppid())
    signal.signal(signal.SIGTERM, lambda *_: STOP_EVENT.set())
    signal.signal(signal.SIGINT, lambda *_: STOP_EVENT.set())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["ARION_ENV"] = "production"
    environment_summary = _load_project_environment()
    os.environ["ARION_ENV"] = "production"
    logger.info(
        "supervisor started role=%s project_root=%s dotenv_present=%s "
        "otrs_legacy_host_source=%s",
        role, PROJECT_ROOT, environment_summary["dotenv_present"],
        environment_summary["otrs_legacy_host_source"],
    )

    try:
        delay = 2
        while not STOP_EVENT.is_set():
            if stop_file.exists():
                STOP_EVENT.set()
                break
            try:
                target_command = _command(role)
            except RuntimeError as exc:
                logger.error("startup refused: %s", sanitize_line(str(exc)))
                return 1
            environment = _child_environment()
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            command = [target_command[0], str(Path(__file__).resolve()), "--run-child", role]
            try:
                child = subprocess.Popen(command, cwd=PROJECT_ROOT / "arion_geo", env=environment,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
            except OSError as exc:
                logger.error("child launch failed: %s", sanitize_line(str(exc)))
                return 1
            try:
                _write_process_record(
                    role, "launcher", child.pid, parent_pid=os.getpid(), executable=target_command[0],
                    created_at=_windows_process_created_at(child._handle) if os.name == "nt" else None,
                )
            except RuntimeError as exc:
                logger.warning("could not confirm launcher process: %s", sanitize_line(str(exc)))
            logger.info("child launcher started (pid %s)", child.pid)
            reader = threading.Thread(target=_read_child_output, args=(child, logger), daemon=True)
            reader.start()
            while child.poll() is None and not STOP_EVENT.wait(0.5):
                if stop_file.exists():
                    STOP_EVENT.set()
            if STOP_EVENT.is_set():
                _stop_child(child, logger, role)
                break
            code = child.wait()
            launcher_record = _read_process_record(role, "launcher", require_running=False)
            # Some Store Python launchers exit after transferring execution to
            # python3.12.exe. Keep supervising that recorded effective process.
            while launcher_record and not STOP_EVENT.is_set():
                service = _read_process_record(role, "child")
                if not service or not _service_process_matches_launcher(launcher_record, service):
                    break
                if stop_file.exists():
                    STOP_EVENT.set()
                    break
                STOP_EVENT.wait(0.5)
            if STOP_EVENT.is_set():
                _stop_child(child, logger, role)
                break
            _remove_process_record(role, "child")
            _remove_process_record(role, "launcher", only_pid=child.pid)
            reader.join(timeout=2)
            child = None
            logger.warning("child exited with status %s; restart in %s seconds", code, delay)
            STOP_EVENT.wait(delay)
            delay = min(delay * 2, 60)
    finally:
        if child is not None:
            _stop_child(child, logger, role)
        for process in ("child", "launcher", "supervisor"):
            _remove_process_record(role, process)
        stop_file.unlink(missing_ok=True)
        logger.info("supervisor stopped")
        lock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARION Windows process supervisor")
    parser.add_argument("--run-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("role", choices=sorted(ROLES))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.run_child:
        return _run_managed_child(args.role)
    if args.status:
        return _print_status(args.role)
    if args.stop:
        return _request_stop(args.role)
    return supervise(args.role)


if __name__ == "__main__":
    raise SystemExit(main())
