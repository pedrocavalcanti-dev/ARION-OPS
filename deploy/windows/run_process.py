"""Windows process supervisor for the ARION web and integration worker tasks."""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv


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


def _stop_path(role: str) -> Path:
    return RUN_ROOT / f"{role}.stop"


def _print_status(role: str) -> int:
    pid_file = _pid_path(role)
    if _is_locked(role):
        try:
            pid = pid_file.read_text(encoding="ascii").strip()
        except OSError:
            pid = "unknown"
        print(f"{role}: running (pid {pid})")
        return 0
    print(f"{role}: stopped")
    return 1


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


def _stop_child(child: subprocess.Popen, logger: logging.Logger) -> None:
    if child.poll() is not None:
        return
    logger.info("requesting child shutdown")
    child.terminate()
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        logger.warning("child did not stop in time; terminating process")
        child.kill()
        child.wait(timeout=10)


def supervise(role: str) -> int:
    logger = _logger(role)
    lock = _lock_file(role)
    if lock is None:
        logger.warning("duplicate start ignored")
        return 0

    child = None
    pid_file = _pid_path(role)
    stop_file = _stop_path(role)
    stop_file.unlink(missing_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    signal.signal(signal.SIGTERM, lambda *_: STOP_EVENT.set())
    signal.signal(signal.SIGINT, lambda *_: STOP_EVENT.set())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["ARION_ENV"] = "production"
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    os.environ["ARION_ENV"] = "production"
    logger.info("supervisor started")

    try:
        delay = 2
        while not STOP_EVENT.is_set():
            if stop_file.exists():
                STOP_EVENT.set()
                break
            try:
                command = _command(role)
            except RuntimeError as exc:
                logger.error("startup refused: %s", sanitize_line(str(exc)))
                return 1
            environment = os.environ.copy()
            environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
            environment["ARION_ENV"] = "production"
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                child = subprocess.Popen(command, cwd=PROJECT_ROOT / "arion_geo", env=environment,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
            except OSError as exc:
                logger.error("child launch failed: %s", sanitize_line(str(exc)))
                return 1
            logger.info("child started (pid %s)", child.pid)
            reader = threading.Thread(target=_read_child_output, args=(child, logger), daemon=True)
            reader.start()
            while child.poll() is None and not STOP_EVENT.wait(0.5):
                if stop_file.exists():
                    STOP_EVENT.set()
            if STOP_EVENT.is_set():
                _stop_child(child, logger)
                break
            code = child.wait()
            reader.join(timeout=2)
            child = None
            logger.warning("child exited with status %s; restart in %s seconds", code, delay)
            STOP_EVENT.wait(delay)
            delay = min(delay * 2, 60)
    finally:
        if child is not None:
            _stop_child(child, logger)
        pid_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        logger.info("supervisor stopped")
        lock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARION Windows process supervisor")
    parser.add_argument("role", choices=sorted(ROLES))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.status:
        return _print_status(args.role)
    if args.stop:
        return _request_stop(args.role)
    return supervise(args.role)


if __name__ == "__main__":
    raise SystemExit(main())
