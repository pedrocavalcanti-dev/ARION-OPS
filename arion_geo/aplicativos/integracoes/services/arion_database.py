"""Bootstrap configuration for the active and candidate Arion databases."""
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured, ValidationError

from .secrets import get_secret_protector


CONFIG_VERSION = 2
CANDIDATE_ALIAS = "arion_candidate"


def project_root():
    return Path(__file__).resolve().parents[4]


def config_path():
    configured = os.environ.get("ARION_DATABASE_CONFIG")
    return Path(configured).expanduser() if configured else project_root() / "var" / "database-config.json"


def default_config(base_dir=None):
    return {"engine": "sqlite", "database": "db.sqlite3", "host": "127.0.0.1", "port": 3306,
            "user": "", "password_protected": "", "timeout": 10}


def _empty_bootstrap():
    return {"version": CONFIG_VERSION, "active": default_config(), "candidate": None,
            "rollback": None, "migration": {}}


def _validate_db_config(value, *, allow_sqlite=True):
    if not isinstance(value, dict) or value.get("engine") not in ({"sqlite", "mysql"} if allow_sqlite else {"mysql"}):
        raise ImproperlyConfigured("O tipo de banco configurado para o Arion não é válido.")
    result = {**default_config(), **value}
    if result["engine"] == "sqlite":
        if not str(result.get("database", "")).strip():
            raise ImproperlyConfigured("O arquivo SQLite configurado para o Arion está vazio.")
    else:
        try:
            result["port"] = int(result["port"])
            result["timeout"] = int(result["timeout"])
        except (TypeError, ValueError):
            raise ImproperlyConfigured("Porta ou timeout inválido na configuração MySQL do Arion.") from None
        if (not str(result.get("host", "")).strip() or not str(result.get("database", "")).strip()
                or not str(result.get("user", "")).strip() or not 1 <= result["port"] <= 65535
                or not 1 <= result["timeout"] <= 60):
            raise ImproperlyConfigured("A configuração MySQL do Banco Arion está incompleta.")
    return result


def _normalize_bootstrap(value):
    # Read the old flat format as an already-active configuration. Never reinterpret
    # a legacy MySQL record as a candidate, which could silently switch the default.
    if "active" not in value:
        active = _validate_db_config(value)
        return {**_empty_bootstrap(), "active": active}
    active = _validate_db_config(value.get("active"))
    candidate = value.get("candidate")
    if candidate is not None:
        candidate = _validate_db_config(candidate, allow_sqlite=False)
    rollback = value.get("rollback")
    if rollback is not None:
        rollback = _validate_db_config(rollback)
    migration = value.get("migration") if isinstance(value.get("migration"), dict) else {}
    return {"version": CONFIG_VERSION, "active": active, "candidate": candidate,
            "rollback": rollback, "migration": migration}


def read_bootstrap_config(*, path=None):
    selected = Path(path) if path else config_path()
    if not selected.exists():
        return _empty_bootstrap(), False
    try:
        raw = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ImproperlyConfigured("O arquivo local de configuração do Banco Arion é inválido.") from None
    if not isinstance(raw, dict):
        raise ImproperlyConfigured("O arquivo local de configuração do Banco Arion é inválido.")
    return _normalize_bootstrap(raw), True


def _write_bootstrap(data, *, path=None):
    selected = Path(path) if path else config_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=selected.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, selected)
    except OSError:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise ValidationError("Não foi possível salvar a configuração bootstrap do Banco Arion.") from None


def _secret_for(config, *, protector=None, label="Banco Arion"):
    if config.get("password_source") == "environment":
        password = os.environ.get("DATABASE_PASSWORD", "")
        if not isinstance(password, str) or not password or "\r" in password or "\n" in password:
            raise ValidationError("Defina DATABASE_PASSWORD no .env local para conectar ao MySQL.")
        return password
    ciphertext = config.get("password_protected", "")
    if not ciphertext:
        raise ValidationError(f"Credencial do candidato {label} ausente.")
    try:
        password = (protector or get_secret_protector()).unprotect(ciphertext)
    except Exception:
        raise ValidationError(f"A credencial {label} protegida por DPAPI não pode ser aberta neste usuário do Windows.") from None
    if not isinstance(password, str) or not password or "\r" in password or "\n" in password:
        raise ValidationError(f"A credencial {label} protegida está inválida.")
    return password


def _password_is_configured(config):
    if config.get("password_source") == "environment":
        return bool(os.environ.get("DATABASE_PASSWORD", ""))
    return bool(config.get("password_protected"))


def active_config(*, path=None, protector=None, decrypt=True):
    bootstrap, exists = read_bootstrap_config(path=path)
    result = dict(bootstrap["active"])
    result["configured"] = exists
    result["password_configured"] = _password_is_configured(result)
    if result["engine"] == "mysql" and decrypt:
        result["password"] = _secret_for(result, protector=protector, label="ativa")
    else:
        result["password"] = ""
    return result


def candidate_config(*, path=None, protector=None, decrypt=True):
    bootstrap, _ = read_bootstrap_config(path=path)
    candidate = bootstrap["candidate"]
    if candidate is None:
        return None
    result = dict(candidate)
    result["password_configured"] = _password_is_configured(result)
    if decrypt:
        result["password"] = _secret_for(result, protector=protector, label="candidata")
    else:
        result["password"] = ""
    return result


def password_for_connection_test(data, *, path=None, protector=None):
    submitted = data.get("password") or ""
    if submitted:
        return submitted
    saved = candidate_config(path=path, protector=protector, decrypt=False)
    same_connection = saved is not None and all(
        str(saved.get(key, "")).strip().casefold() == str(data.get(key, "")).strip().casefold()
        for key in ("host", "port", "database", "user")
    )
    if not same_connection:
        raise ValidationError("Informe a senha do candidato MySQL para testar esta conexão.")
    return _secret_for(saved, protector=protector, label="candidata")


def local_config(*, path=None, protector=None, decrypt=True):
    """Compatibility alias for the active configuration; never returns candidate settings."""
    return active_config(path=path, protector=protector, decrypt=decrypt)


def candidate_fingerprint(config):
    public = {key: config.get(key) for key in ("engine", "host", "port", "database", "user", "timeout")}
    protected = config.get("password_protected", "")
    environment_secret = ""
    if config.get("password_source") == "environment":
        environment_secret = hashlib.sha256(
            os.environ.get("DATABASE_PASSWORD", "").encode("utf-8")
        ).hexdigest()
    payload = json.dumps({"config": public, "protected_secret": protected,
                          "environment_secret_fingerprint": environment_secret},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _same_target(left, right):
    return all(str(left.get(key, "")).strip().casefold() == str(right.get(key, "")).strip().casefold()
               for key in ("host", "port", "database", "user"))


def save_candidate_config(data, *, path=None, protector=None):
    bootstrap, _ = read_bootstrap_config(path=path)
    if data.get("engine", "mysql") != "mysql":
        raise ValidationError("O banco candidato desta migração deve ser MySQL.")
    try:
        port, timeout = int(data.get("port", 3306)), int(data.get("timeout", 10))
    except (TypeError, ValueError):
        raise ValidationError("Porta ou timeout MySQL inválido.") from None
    host, database, user = (str(data.get(key, "")).strip() for key in ("host", "database", "user"))
    if not host or not database or not user or not 1 <= port <= 65535 or not 1 <= timeout <= 60:
        raise ValidationError("Revise host, porta, schema, usuário e timeout do candidato MySQL.")
    active = bootstrap["active"]
    if active["engine"] == "mysql" and _same_target(active, {"host": host, "port": port, "database": database, "user": user}):
        raise ValidationError("O banco candidato deve ser diferente do banco MySQL atualmente ativo.")
    previous = bootstrap["candidate"] or {}
    password = data.get("password") or ""
    if password and (not isinstance(password, str) or "\r" in password or "\n" in password):
        raise ValidationError("A senha MySQL informada está inválida.")
    same_target = _same_target(previous, {"host": host, "port": port, "database": database, "user": user})
    password_source = ""
    if password:
        try:
            protected = (protector or get_secret_protector()).protect(password)
        except Exception:
            raise ValidationError("O armazenamento protegido de senhas não está disponível neste Windows.") from None
        password_source = "dpapi"
    elif os.environ.get("DATABASE_PASSWORD", ""):
        protected = ""
        password_source = "environment"
    elif same_target and previous.get("password_source") == "environment":
        protected = ""
        password_source = "environment"
    elif same_target and previous.get("password_protected"):
        protected = previous["password_protected"]
        password_source = previous.get("password_source") or "dpapi"
        # Do not preserve an unreadable blob from another Windows user.
        _secret_for({"password_protected": protected}, protector=protector, label="candidata")
    else:
        raise ValidationError("Defina DATABASE_PASSWORD no .env local antes de salvar o candidato MySQL.")
    candidate = {"engine": "mysql", "host": host, "port": port, "database": database,
                 "user": user, "timeout": timeout, "password_protected": protected,
                 "password_source": password_source}
    old_fingerprint = candidate_fingerprint(previous) if previous else None
    new_fingerprint = candidate_fingerprint(candidate)
    if new_fingerprint != old_fingerprint:
        bootstrap["migration"] = {"candidate_fingerprint": new_fingerprint,
                                  "connection_tested": False, "schema_prepared": False,
                                  "data_copied": False, "validation": None}
    bootstrap["candidate"] = candidate
    _write_bootstrap(bootstrap, path=path)
    return {key: value for key, value in candidate.items() if key != "password_protected"} | {
        "password_configured": _password_is_configured(candidate)}


def record_candidate_test(*, path=None):
    bootstrap, _ = read_bootstrap_config(path=path)
    candidate = bootstrap["candidate"]
    if candidate is None:
        raise ValidationError("Salve uma configuração MySQL candidata antes do teste.")
    fingerprint = candidate_fingerprint(candidate)
    state = bootstrap["migration"]
    if state.get("candidate_fingerprint") != fingerprint:
        state = {"candidate_fingerprint": fingerprint, "connection_tested": False,
                 "schema_prepared": False, "data_copied": False, "validation": None}
    state["connection_tested"] = True
    bootstrap["migration"] = state
    _write_bootstrap(bootstrap, path=path)


def migration_state(*, path=None):
    bootstrap, _ = read_bootstrap_config(path=path)
    state = dict(bootstrap.get("migration", {}))
    fingerprint = candidate_fingerprint(bootstrap["candidate"]) if bootstrap["candidate"] else None
    if state.get("candidate_fingerprint") != fingerprint:
        return {"connection_tested": False, "schema_prepared": False, "data_copied": False,
                "validated": False, "validation": None}
    validation = state.get("validation") or {}
    return {"connection_tested": bool(state.get("connection_tested")),
            "schema_prepared": bool(state.get("schema_prepared")),
            "data_copied": bool(state.get("data_copied")),
            "validated": bool(validation.get("passed")), "validation": validation}


def mark_migration_stage(stage, *, path=None, validation=None):
    allowed = {"schema_prepared", "data_copied"}
    if stage not in allowed:
        raise ValidationError("Etapa de migração inválida.")
    bootstrap, _ = read_bootstrap_config(path=path)
    candidate = bootstrap["candidate"]
    state = bootstrap["migration"]
    if candidate is None or state.get("candidate_fingerprint") != candidate_fingerprint(candidate):
        raise ValidationError("Teste novamente o candidato após salvá-lo.")
    if not state.get("connection_tested"):
        raise ValidationError("Teste a conexão do candidato antes de preparar ou migrar dados.")
    state[stage] = True
    if stage == "data_copied":
        state["validation"] = None
    if validation is not None:
        state["validation"] = validation
    bootstrap["migration"] = state
    _write_bootstrap(bootstrap, path=path)


def activate_candidate(*, path=None, environ=None):
    environment = environ if environ is not None else os.environ
    if environment.get("DATABASE_URL", "").strip():
        raise ValidationError("DATABASE_URL tem precedência. Remova-a antes de ativar o banco candidato.")
    bootstrap, _ = read_bootstrap_config(path=path)
    candidate = bootstrap["candidate"]
    state = bootstrap["migration"]
    fingerprint = candidate_fingerprint(candidate) if candidate else None
    validation = state.get("validation") or {}
    if not candidate or state.get("candidate_fingerprint") != fingerprint:
        raise ValidationError("Salve e teste um banco candidato antes de ativá-lo.")
    if not all(state.get(key) for key in ("connection_tested", "schema_prepared", "data_copied")) or not validation.get("passed"):
        raise ValidationError("A ativação está bloqueada: prepare o schema, migre os dados e valide todas as verificações.")
    bootstrap["rollback"] = bootstrap["active"]
    bootstrap["active"] = candidate
    bootstrap["candidate"] = None
    bootstrap["migration"] = {"activated_at": datetime.now(timezone.utc).isoformat(),
                              "activated_engine": "mysql"}
    _write_bootstrap(bootstrap, path=path)


def rollback_to_previous(*, path=None):
    if os.environ.get("DATABASE_URL", "").strip():
        raise ValidationError("DATABASE_URL tem precedencia sobre o rollback local. Remova-a do ambiente antes de reverter.")
    bootstrap, _ = read_bootstrap_config(path=path)
    previous = bootstrap.get("rollback")
    if previous is None and bootstrap["active"]["engine"] == "mysql":
        sqlite_file = project_root() / "arion_geo" / "db.sqlite3"
        if sqlite_file.is_file():
            previous = {**default_config(), "database": "db.sqlite3"}
    if previous is None or previous.get("engine") != "sqlite":
        raise ValidationError("Não há uma configuração SQLite de rollback registrada.")
    sqlite_path = Path(previous["database"]).expanduser()
    if not sqlite_path.is_absolute():
        sqlite_path = project_root() / "arion_geo" / sqlite_path
    if str(previous["database"]) != ":memory:" and not sqlite_path.exists():
        raise ValidationError("O arquivo SQLite de rollback não foi encontrado; nada foi alterado.")
    bootstrap["active"], bootstrap["rollback"] = previous, bootstrap["active"]
    bootstrap["candidate"] = None
    bootstrap["migration"] = {}
    _write_bootstrap(bootstrap, path=path)


def candidate_test_is_current(*, path=None):
    bootstrap, _ = read_bootstrap_config(path=path)
    candidate = bootstrap["candidate"]
    state = bootstrap["migration"]
    return bool(candidate and state.get("connection_tested")
                and state.get("candidate_fingerprint") == candidate_fingerprint(candidate))


def _is_loopback_mysql_host(host):
    normalized = str(host or "").strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _verified_mysql_ssl_context(ca_file):
    ca_path = Path(str(ca_file or "")).expanduser()
    if not ca_path.is_absolute():
        ca_path = project_root() / ca_path
    try:
        if not ca_path.is_file():
            raise OSError
        context = ssl.create_default_context(cafile=str(ca_path.resolve()))
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        return context
    except Exception:
        raise ImproperlyConfigured(
            "O certificado CA do MySQL não existe ou não é válido; configure DB_SSL_CA no .env local."
        ) from None


def _configured_database_ca(environ=None):
    environment = environ if environ is not None else os.environ
    return str(environment.get("DB_SSL_CA", "") or "").strip()


def _mysql_database_dict(*, name, user="", password="", host="127.0.0.1", port=3306, timeout=10, ssl_ca=""):
    try:
        port, timeout = int(port), int(timeout)
    except (TypeError, ValueError):
        raise ImproperlyConfigured("Porta ou timeout inválido na configuração do Banco Arion.") from None
    if not name or not host or not 1 <= port <= 65535 or not 1 <= timeout <= 60:
        raise ImproperlyConfigured("A configuração MySQL do Banco Arion está incompleta.")
    options = {"charset": "utf8mb4", "connect_timeout": timeout,
               "read_timeout": timeout, "write_timeout": timeout}
    if not _is_loopback_mysql_host(host) and not ssl_ca:
        raise ImproperlyConfigured(
            "Conexões MySQL remotas exigem TLS com CA validada; configure DB_SSL_CA no .env local."
        )
    if ssl_ca:
        options["ssl"] = _verified_mysql_ssl_context(ssl_ca)
    return {"ENGINE": "django.db.backends.mysql", "NAME": name, "USER": user, "PASSWORD": password,
            "HOST": host, "PORT": str(port), "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": True,
            "OPTIONS": options}


def _from_database_url(value, *, environ=None):
    environment = environ if environ is not None else os.environ
    parsed = urlsplit(value)
    scheme = parsed.scheme.casefold()
    if scheme in {"mysql", "mysql+pymysql"}:
        query = parse_qs(parsed.query, keep_blank_values=True)
        url_password = unquote(parsed.password or "")
        environment_password = environment.get("DATABASE_PASSWORD", "")
        if url_password and environment_password:
            raise ImproperlyConfigured(
                "Defina a senha do MySQL somente em DATABASE_PASSWORD ou na URL, não nos dois."
            )
        password = url_password or environment_password
        ssl_ca = ((query.get("ssl_ca") or [""])[0].strip()
                  or _configured_database_ca(environment))
        ssl_mode = (query.get("ssl_mode") or [""])[0].strip().casefold().replace("-", "_")
        if ssl_mode and ssl_mode != "verify_identity":
            raise ImproperlyConfigured(
                "MySQL remoto exige ssl_mode=VERIFY_IDENTITY para validar CA e nome do servidor."
            )
        if ssl_mode == "verify_identity" and not ssl_ca:
            raise ImproperlyConfigured(
                "ssl_mode=VERIFY_IDENTITY exige o caminho do certificado em ssl_ca."
            )
        host = parsed.hostname or ""
        if not _is_loopback_mysql_host(host) and not password:
            raise ImproperlyConfigured(
                "DATABASE_PASSWORD está ausente para a conexão MySQL remota configurada."
            )
        return _mysql_database_dict(name=unquote(parsed.path.lstrip("/")), user=unquote(parsed.username or ""),
                                    password=password, host=host, port=parsed.port or 3306,
                                    timeout=(query.get("connect_timeout") or [10])[0], ssl_ca=ssl_ca)
    if scheme == "sqlite":
        raw_path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc != "localhost":
            raw_path = f"//{parsed.netloc}{raw_path}"
        if os.name == "nt" and len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        database = ":memory:" if raw_path in {"", "/:memory:"} else raw_path
        if database != ":memory:" and not Path(database).is_absolute():
            database = str(project_root() / database.lstrip("/"))
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": database}
    raise ImproperlyConfigured("DATABASE_URL aceita somente esquemas SQLite ou MySQL.")


def django_database_settings(*, base_dir, environ=None, path=None, protector=None):
    environment = environ if environ is not None else os.environ
    explicit_url = environment.get("DATABASE_URL", "").strip()
    if explicit_url:
        return _from_database_url(explicit_url, environ=environment)
    active = active_config(path=path, protector=protector)
    if active["engine"] == "mysql":
        return _mysql_database_dict(name=active["database"], user=active["user"], password=active["password"],
                                    host=active["host"], port=active["port"], timeout=active["timeout"],
                                    ssl_ca=_configured_database_ca(environment))
    database = Path(active["database"]).expanduser()
    if not database.is_absolute() and str(database) != ":memory:":
        database = Path(base_dir) / database
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:" if str(database) == ":memory:" else database}


def candidate_database_settings(*, path=None, protector=None):
    candidate = candidate_config(path=path, protector=protector, decrypt=True)
    if candidate is None:
        raise ValidationError("Nenhum MySQL candidato foi salvo.")
    return _mysql_database_dict(name=candidate["database"], user=candidate["user"],
                                password=candidate["password"], host=candidate["host"],
                                port=candidate["port"], timeout=candidate["timeout"],
                                ssl_ca=_configured_database_ca())


def active_database_summary(database_settings):
    engine = database_settings.get("ENGINE", "")
    is_mysql = engine == "django.db.backends.mysql"
    name = database_settings.get("NAME", "")
    return {"engine": "MySQL" if is_mysql else "SQLite", "name": Path(str(name)).name if not is_mysql else str(name),
            "source": "DATABASE_URL" if os.environ.get("DATABASE_URL", "").strip() else "local"}


def test_sqlite_connection(database, *, base_dir=None):
    path = Path(database).expanduser()
    if not path.is_absolute() and str(path) != ":memory:":
        path = (Path(base_dir) if base_dir else project_root() / "arion_geo") / path
    if str(path) == ":memory:":
        raise ValidationError("Para testar SQLite, informe um arquivo existente.")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise ValidationError("Não foi possível abrir o arquivo SQLite selecionado para leitura.") from None
    return {"database": path.name, "version": sqlite3.sqlite_version}


def test_mysql_connection(config, password, *, connection_factory=None):
    if config.get("engine") != "mysql":
        raise ValidationError("O teste de conexão MySQL exige MySQL selecionado.")
    try:
        if connection_factory is None:
            import pymysql
            connection_factory = pymysql.connect
        database_settings = _mysql_database_dict(
            name=str(config["database"]).strip(), user=str(config["user"]).strip(), password=password,
            host=str(config["host"]).strip(), port=int(config.get("port", 3306)),
            timeout=int(config.get("timeout", 10)),
            ssl_ca=config.get("ssl_ca") or _configured_database_ca(),
        )
        connection = connection_factory(
            host=database_settings["HOST"], port=int(database_settings["PORT"]),
            user=database_settings["USER"], password=database_settings["PASSWORD"],
            database=database_settings["NAME"], **database_settings["OPTIONS"],
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE(), VERSION(), NOW()")
                database, version, _server_time = cursor.fetchone()
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                _cipher_name, cipher = cursor.fetchone()
        finally:
            connection.close()
        tls_verified = bool(database_settings["OPTIONS"].get("ssl"))
        if tls_verified and not cipher:
            raise ValidationError("O servidor MySQL não confirmou uma sessão TLS ativa.")
        if "mariadb" in str(version).casefold():
            raise ValidationError("O servidor respondeu como MariaDB; o Banco Arion oferece suporte oficial a MySQL e SQLite.")
        return {"database": database, "version": str(version), "tls_verified": tls_verified}
    except ValidationError:
        raise
    except ImproperlyConfigured as exc:
        raise ValidationError(str(exc)) from None
    except Exception as exc:
        code = exc.args[0] if getattr(exc, "args", None) else None
        if code == 1045:
            message = "Acesso negado pelo MySQL. Verifique usu\u00e1rio, senha e permiss\u00f5es."
        elif code == 1049:
            message = "O schema MySQL informado não existe. Crie-o antes de preparar as migrations."
        elif code == 1044:
            message = "O usuário MySQL não tem acesso ao schema informado."
        elif isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc).casefold():
            message = "O teste de conexão MySQL excedeu o tempo limite."
        elif code == 2003 and "refused" in str(exc).casefold():
            message = "A conexão com o host MySQL foi recusada."
        elif code in {2002, 2003, 2005}:
            message = "Não foi possível alcançar o host MySQL informado."
        elif code == 2026:
            message = "Falha na validação TLS do MySQL. Confira a CA oficial e o hostname configurado."
        else:
            message = "Falha inesperada ao testar a conexão MySQL."
        raise ValidationError(message) from None
