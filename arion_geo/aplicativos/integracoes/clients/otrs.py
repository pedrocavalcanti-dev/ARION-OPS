"""Read-only metadata and sample client for a separately configured OTRS MySQL database."""
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import os
from pathlib import Path
import ssl

from django.conf import settings

from aplicativos.integracoes.services.errors import (
    IntegrationError,
    IntegrationNotConfigured,
    OTRSAuthenticationError,
    OTRSConnectionRefusedError,
    OTRSDatabaseError,
    OTRSDatabaseNotFoundError,
    OTRSCredentialInvalidError,
    OTRSCredentialMissingError,
    OTRSDriverError,
    OTRSHostUnreachableError,
    OTRSTLSCertificateError,
    OTRSTLSError,
    OTRSTLSHandshakeError,
    OTRSTLSRequiredError,
    OTRSTimeoutError,
    OTRSUnavailableError,
)


logger = logging.getLogger(__name__)

SENSITIVE_PARTS = (
    "password", "passwd", "token", "secret", "session", "cookie", "auth", "credential",
    "apikey", "privatekey", "customeruser", "article", "body", "internalnote", "notebody",
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]+$")
_WRITE_OR_SIDE_EFFECT = re.compile(
    r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|REPLACE|RENAME|GRANT|REVOKE|CALL|LOAD|INTO|OUTFILE|DUMPFILE|LOCK|UNLOCK|SLEEP|GET_LOCK|RELEASE_LOCK)\b|\bFOR\s+UPDATE\b",
    re.IGNORECASE,
)
_FORBIDDEN_DATA = re.compile(
    r"\b(pw|password|passwd|pwd|secret|token|credential|session|cookie|auth|body|html_body|mime_content|attachment_content|article\w*|customer_user|internal_note|note_body)\b",
    re.IGNORECASE,
)


class OTRSReadOnlyClient:
    MAX_PAGE_SIZE = 50

    LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    def __init__(
        self, *, host, port=3306, database="", username, password, timeout=10,
        use_tls=None, ssl_ca="", ssl_verify=True, allow_insecure_remote=False,
        connection_factory=None,
    ):
        if not host:
            raise IntegrationNotConfigured()
        if not isinstance(username, str) or not username or password is None or password == "":
            raise OTRSCredentialMissingError()
        if not isinstance(password, str) or "\r" in password or "\n" in password:
            raise OTRSCredentialInvalidError()
        self.host = str(host).strip()
        self.is_loopback_host = self.host.casefold() in self.LOOPBACK_HOSTS
        self.port = int(3306 if port in (None, "") else port)
        self.database = (database or "").strip()
        self.username = username
        self.password = password
        self.timeout = max(1, min(int(10 if timeout in (None, "") else timeout), 60))
        self.use_tls = (not self.is_loopback_host) if use_tls is None else bool(use_tls)
        self.ssl_ca = str(ssl_ca or os.getenv("ARION_OTRS_SSL_CA") or "").strip()
        self.ssl_verify = True if ssl_verify is None else bool(ssl_verify)
        self.allow_insecure_remote = False if allow_insecure_remote is None else bool(allow_insecure_remote)
        self.legacy_no_tls_host = str(getattr(settings, "OTRS_LEGACY_NO_TLS_HOST", "") or "").strip().casefold()
        self.connection_factory = connection_factory
        self._active_connection = None
        self.select_queries_count = 0

    def test_connection(self):
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DATABASE() AS database_name, VERSION() AS server_version, NOW() AS server_time"
                )
                row = cursor.fetchone()
                if not row:
                    raise OTRSDatabaseError()
                database_name = self._row_value(row, "database_name")
                if self.database and database_name != self.database:
                    raise OTRSDatabaseNotFoundError()
        return True

    def list_databases(self):
        with self._connection() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME")
                    rows = cursor.fetchall()
                except Exception as exc:
                    if self._error_code(exc) in {1044, 1142, 1227}:
                        return []
                    raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None
        return [self._row_value(row, "SCHEMA_NAME") for row in rows if self._row_value(row, "SCHEMA_NAME")]

    def list_tables(self, database=None):
        selected = self._validated_database(database)
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME", (selected,))
                rows = cursor.fetchall()
        return [{"name": self._row_value(row, "TABLE_NAME"), "type": self._row_value(row, "TABLE_TYPE"), "estimated_rows": self._row_value(row, "TABLE_ROWS")} for row in rows]

    def get_table_schema(self, table_name, database=None):
        selected = self._validated_database(database)
        table = self._validated_table(table_name, selected)
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (selected, table))
                rows = cursor.fetchall()
        return [{"column": self._row_value(row, "COLUMN_NAME"), "type": self._row_value(row, "COLUMN_TYPE"), "nullable": self._row_value(row, "IS_NULLABLE"), "key": self._row_value(row, "COLUMN_KEY"), "default": self._display_value(self._row_value(row, "COLUMN_DEFAULT")), "extra": self._row_value(row, "EXTRA")} for row in rows]

    def get_table_indexes(self, table_name, database=None):
        selected = self._validated_database(database)
        table = self._validated_table(table_name, selected)
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX", (selected, table))
                rows = cursor.fetchall()
        return [{"name": self._row_value(row, "INDEX_NAME"), "unique": not bool(self._row_value(row, "NON_UNIQUE")), "sequence": self._row_value(row, "SEQ_IN_INDEX"), "column": self._row_value(row, "COLUMN_NAME"), "type": self._row_value(row, "INDEX_TYPE")} for row in rows]

    def get_foreign_keys(self, table_name, database=None):
        selected = self._validated_database(database)
        table = self._validated_table(table_name, selected)
        sql = "SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION"
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (selected, table))
                rows = cursor.fetchall()
        return [{"constraint": self._row_value(row, "CONSTRAINT_NAME"), "column": self._row_value(row, "COLUMN_NAME"), "referenced_schema": self._row_value(row, "REFERENCED_TABLE_SCHEMA"), "referenced_table": self._row_value(row, "REFERENCED_TABLE_NAME"), "referenced_column": self._row_value(row, "REFERENCED_COLUMN_NAME")} for row in rows]

    def get_server_metadata(self):
        """Return non-secret server/database metadata for a read-only study."""
        sql = (
            "SELECT DATABASE() AS database_name, VERSION() AS server_version, "
            "@@character_set_database AS character_set, @@collation_database AS collation"
        )
        rows = self.execute_select(sql)
        return rows[0] if rows else {}

    def get_all_columns(self, database=None):
        """Fetch schema metadata in one bounded metadata query (no table contents)."""
        selected = self._validated_database(database)
        sql = (
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, "
            "ORDINAL_POSITION FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        return self.execute_select(sql, (selected,))

    def execute_select(self, sql, params=()):
        """Execute one SELECT only; deny sensitive columns and side-effect SQL."""
        statement = str(sql or "").strip()
        if not statement or not re.match(r"^SELECT\b", statement, re.IGNORECASE):
            raise OTRSDatabaseError()
        normalized_statement = re.sub(r"[^A-Za-z0-9]", "", statement).casefold()
        forbidden_parts = (*SENSITIVE_PARTS, "pw", "pwd", "htmlbody", "mimecontent", "attachmentcontent")
        if (";" in statement or _WRITE_OR_SIDE_EFFECT.search(statement) or _FORBIDDEN_DATA.search(statement)
                or any(part in normalized_statement for part in forbidden_parts)):
            raise OTRSDatabaseError()
        if re.search(r"\bSELECT\s+\*", statement, re.IGNORECASE):
            raise OTRSDatabaseError()
        self.select_queries_count += 1
        with self._connection() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(statement, tuple(params or ()))
                    rows = cursor.fetchall()
                except Exception as exc:
                    raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None
        return [dict(row) if isinstance(row, dict) else row for row in rows]

    def fetch_quality_articles(self, queue_name, *, after_article_id=0, limit=100):
        """Read a bounded batch of article bodies only for one exact queue.

        This is intentionally separate from execute_select: article contents are
        sensitive and are exposed only transiently to the QID parser.
        """
        queue_name = str(queue_name or "").strip()
        if not queue_name:
            raise OTRSDatabaseError()
        try:
            after_article_id = max(0, int(after_article_id))
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            raise OTRSDatabaseError() from None
        columns = self.get_all_columns()
        schema = {}
        for row in columns:
            table = str(self._row_value(row, "TABLE_NAME") or "").casefold()
            column = str(self._row_value(row, "COLUMN_NAME") or "")
            if table and column:
                schema.setdefault(table, set()).add(column.casefold())
        required = {
            "queue": {"id", "name"},
            "ticket": {"id", "tn", "queue_id", "ticket_state_id"},
            "ticket_state": {"id", "name"},
            "article": {"id", "ticket_id"},
            "article_data_mime": {"article_id", "a_body"},
        }
        if any(not names.issubset(schema.get(table, set())) for table, names in required.items()):
            raise OTRSDatabaseError()

        queue_rows = self.execute_select(
            "SELECT id AS queue_id FROM queue WHERE name = %s LIMIT 2", (queue_name,),
        )
        if len(queue_rows) != 1:
            raise OTRSDatabaseError()
        queue_id = self._row_value(queue_rows[0], "queue_id")
        sql = (
            "SELECT a.id AS article_id, t.id AS ticket_id, t.tn AS ticket_number, "
            "t.queue_id AS queue_id, q.name AS queue_name, t.ticket_state_id AS state_id, "
            "ts.name AS state_name, m.a_body AS article_body "
            "FROM article a INNER JOIN ticket t ON t.id = a.ticket_id "
            "INNER JOIN queue q ON q.id = t.queue_id "
            "INNER JOIN ticket_state ts ON ts.id = t.ticket_state_id "
            "INNER JOIN article_data_mime m ON m.article_id = a.id "
            "WHERE t.queue_id = %s AND a.id > %s ORDER BY a.id ASC LIMIT %s"
        )
        self.select_queries_count += 1
        with self._connection() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(sql, (queue_id, after_article_id, limit))
                    rows = cursor.fetchall()
                except Exception as exc:
                    raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None
        return [dict(row) if isinstance(row, dict) else row for row in rows]

    def find_quality_ticket_article_by_qid(self, ticket_number, correlation_id, *, limit=20):
        """Bounded read-only reconciliation lookup by ticket number and exact body QID."""
        ticket_number = str(ticket_number or "").strip()
        correlation_id = str(correlation_id or "").strip().upper()
        if (not ticket_number or len(ticket_number) > 80
                or not re.fullmatch(r"ARION-QID-[0-9A-F]{32}", correlation_id)):
            raise OTRSDatabaseError()
        limit = max(1, min(int(limit), 20))
        sql = (
            "SELECT a.id AS article_id, a.create_time AS article_time, t.id AS ticket_id, t.tn AS ticket_number, "
            "t.queue_id AS queue_id, q.name AS queue_name, t.ticket_state_id AS state_id, "
            "ts.name AS state_name, m.a_body AS article_body "
            "FROM article a INNER JOIN ticket t ON t.id = a.ticket_id "
            "INNER JOIN queue q ON q.id = t.queue_id "
            "INNER JOIN ticket_state ts ON ts.id = t.ticket_state_id "
            "INNER JOIN article_data_mime m ON m.article_id = a.id "
            "WHERE t.tn = %s AND m.a_body LIKE %s ORDER BY a.id ASC LIMIT %s"
        )
        self.select_queries_count += 1
        with self._connection() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(sql, (ticket_number, f"%{correlation_id}%", limit))
                    rows = cursor.fetchall()
                except Exception as exc:
                    raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None
        return [dict(row) if isinstance(row, dict) else row for row in rows]

    def fetch_quality_ticket_states(self, ticket_ids, *, queue_names, limit=200):
        """Refresh metadata for already linked tickets, without reading articles."""
        ids = tuple(dict.fromkeys(int(value) for value in ticket_ids if int(value) > 0))
        names = tuple(dict.fromkeys(str(value).strip() for value in queue_names if str(value).strip()))
        limit = max(1, min(int(limit), 200))
        if not ids or not names:
            return []
        columns = self.get_all_columns()
        schema = {}
        for row in columns:
            table = str(self._row_value(row, "TABLE_NAME") or "").casefold()
            column = str(self._row_value(row, "COLUMN_NAME") or "")
            if table and column:
                schema.setdefault(table, set()).add(column.casefold())
        required = {
            "queue": {"id", "name"}, "ticket": {"id", "tn", "queue_id", "ticket_state_id"},
            "ticket_state": {"id", "name"},
        }
        if any(not fields.issubset(schema.get(table, set())) for table, fields in required.items()):
            raise OTRSDatabaseError()
        id_marks = ",".join(["%s"] * min(len(ids), limit))
        sql = (
            "SELECT t.id AS ticket_id, t.tn AS ticket_number, t.queue_id AS queue_id, "
            "q.name AS queue_name, t.ticket_state_id AS state_id, ts.name AS state_name "
            "FROM ticket t INNER JOIN queue q ON q.id = t.queue_id "
            "INNER JOIN ticket_state ts ON ts.id = t.ticket_state_id "
            f"WHERE t.id IN ({id_marks}) LIMIT %s"
        )
        # Query the exact linked IDs across queues. Absence from this bounded
        # lookup can then be recorded as unavailable; queue changes remain visible.
        return self.execute_select(sql, (*ids[:limit], limit))

    @contextmanager
    def read_only_session(self):
        """Reuse one read-only connection across a sequential study run."""
        if self._active_connection is not None:
            yield self
            return
        with self._connection() as connection:
            self._active_connection = connection
            try:
                yield self
            finally:
                self._active_connection = None

    def quote_table(self, table_name, database=None):
        table = self._validated_table(table_name, self._validated_database(database))
        return f"`{table}`"

    def quote_column(self, table_name, column_name, database=None):
        table = self._validated_table(table_name, self._validated_database(database))
        columns = {row["column"] for row in self.get_table_schema(table, database)}
        column = str(column_name or "")
        if column not in columns or not _IDENTIFIER.fullmatch(column):
            raise OTRSDatabaseError()
        return f"`{column}`"

    def get_sample_rows(self, table_name, *, database=None, page=1, page_size=25):
        selected = self._validated_database(database)
        table = self._validated_table(table_name, selected)
        normalized_table = "".join(char.lower() for char in table if char.isalnum())
        if "article" in normalized_table or "customeruser" in normalized_table:
            raise OTRSDatabaseError()
        try:
            page = max(1, min(int(page), 10000))
            page_size = max(1, min(int(page_size), self.MAX_PAGE_SIZE))
        except (TypeError, ValueError):
            raise OTRSDatabaseError() from None
        offset = (page - 1) * page_size
        # Table names are accepted only after discovery and escaped as MySQL identifiers.
        quoted = "`" + table.replace("`", "``") + "`"
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {quoted} LIMIT %s OFFSET %s", (page_size, offset))
                rows = cursor.fetchall()
        return [self._sanitize_row(row) for row in rows]

    @contextmanager
    def _connection(self):
        if self._active_connection is not None:
            try:
                yield self._active_connection
            except IntegrationError:
                raise
            except Exception as exc:
                raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None
            return
        connection = None
        try:
            factory = self.connection_factory or self._default_connection_factory
            connection = factory()
            # Set the session default to read-only before any metadata/sample query.
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
            yield connection
        except IntegrationError:
            raise
        except Exception as exc:
            raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _build_ssl_context(self):
        if not self.is_loopback_host:
            if not self.use_tls:
                if (
                    not self.allow_insecure_remote
                    or not self.legacy_no_tls_host
                    or self.host.casefold() != self.legacy_no_tls_host
                ):
                    raise OTRSTLSRequiredError(
                        error_code="tls_policy_required",
                        technical_detail=(
                            "Transporte sem TLS exige opt-in explícito e correspondência ao host legado autorizado; "
                            "habilite TLS verificado ou utilize o túnel local configurado."
                        ),
                    )
                logger.warning(
                    "OTRS remoto usando transporte MySQL sem TLS por configuração explícita de compatibilidade legada."
                )
                return None
            if not self.ssl_verify:
                raise OTRSTLSRequiredError(
                    error_code="tls_policy_required",
                    technical_detail=(
                        "Política TLS obrigatória para host remoto (ssl_verify=False); "
                        "habilite TLS com verificação de certificado ou conecte via túnel local em 127.0.0.1."
                    ),
                )
        if not self.use_tls:
            return None
        cafile = None
        if self.ssl_ca:
            ca_path = Path(self.ssl_ca).expanduser()
            if not ca_path.is_file():
                raise OTRSTLSCertificateError(
                    safe_message=(
                        "Arquivo de certificado CA configurado para o OTRS não foi encontrado. "
                        "Verifique ssl_ca / ARION_OTRS_SSL_CA."
                    ),
                    error_code="tls_ca_not_found",
                    technical_detail="O caminho configurado em ssl_ca / ARION_OTRS_SSL_CA não existe ou não é um arquivo regular.",
                )
            cafile = str(ca_path)
        try:
            context = ssl.create_default_context(cafile=cafile)
        except (ssl.SSLError, OSError) as exc:
            detail = self._sanitize_tls_detail(exc, default="arquivo CA inválido", redact_values=(self.password, self.username))
            raise OTRSTLSCertificateError(
                safe_message=(
                    "Não foi possível carregar o certificado CA configurado para o OTRS. "
                    "Verifique o formato PEM em ssl_ca / ARION_OTRS_SSL_CA."
                ),
                error_code="tls_ca_invalid",
                technical_detail=f"Falha ao carregar CA TLS ({detail}).",
            ) from None
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if self.ssl_verify:
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = bool(cafile)
        return context

    def _default_connection_factory(self):
        ssl_context = self._build_ssl_context()
        try:
            import pymysql
        except ImportError:
            raise OTRSUnavailableError() from None
        options = {
            "host": self.host, "port": self.port, "user": self.username,
            "password": self.password, "connect_timeout": self.timeout,
            "read_timeout": self.timeout, "write_timeout": self.timeout,
            "charset": "utf8mb4", "autocommit": True,
            "cursorclass": pymysql.cursors.DictCursor,
        }
        if ssl_context is not None:
            options["ssl"] = ssl_context
        else:
            options["ssl_disabled"] = True
        if self.database:
            options["database"] = self.database
        try:
            return pymysql.connect(**options)
        except IntegrationError:
            raise
        except Exception as exc:
            raise self._mapped_error(exc, redact_values=(self.password, self.username)) from None

    def _validated_database(self, requested):
        database = (requested if requested is not None else self.database) or ""
        database = str(database).strip()
        if not database:
            raise OTRSDatabaseError()
        available = self.list_databases()
        # If metadata visibility is restricted, allow an explicitly configured name;
        # all schema lookups still pass it as a parameter and MySQL enforces grants.
        if available and database not in available:
            raise OTRSDatabaseError()
        return database

    def _validated_table(self, table_name, database):
        table = str(table_name or "")
        available = {row["name"] for row in self.list_tables(database)}
        if table not in available or not _IDENTIFIER.fullmatch(table):
            raise OTRSDatabaseError()
        return table

    @staticmethod
    def _row_value(row, key):
        if isinstance(row, dict):
            return row.get(key, row.get(key.lower()))
        try:
            return row[0]
        except (TypeError, IndexError, KeyError):
            return None

    @classmethod
    def _sanitize_row(cls, row):
        if not isinstance(row, dict):
            return {}
        sanitized = {}
        for key, value in row.items():
            normalized = "".join(char.lower() for char in str(key) if char.isalnum())
            sensitive = normalized == "pw" or normalized.endswith("pw") or any(part in normalized for part in SENSITIVE_PARTS)
            sanitized[str(key)] = "••••••" if sensitive else cls._display_value(value)
        return sanitized

    @staticmethod
    def _display_value(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, bytes):
            return "[binário]"
        return str(value)[:500]

    @staticmethod
    def _error_code(exc):
        args = getattr(exc, "args", ())
        return args[0] if args and isinstance(args[0], int) else None

    @classmethod
    def _sanitize_tls_detail(cls, exc, *, default="", redact_values=()):
        code = cls._error_code(exc)
        original = getattr(exc, "original_exception", None) or getattr(exc, "__cause__", None)
        target = original if isinstance(original, ssl.SSLError) else exc
        parts = [str(part) for part in getattr(target, "args", ()) if not isinstance(part, int)]
        text = " ".join(parts).strip() or str(target or "").strip()
        for secret in sorted((str(val) for val in redact_values if val), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\")]+", "[REDACTED_URL]", text)
        text = re.sub(
            r"(?i)\b(password|passwd|pwd|secret|token|access_token|refresh_token|client_secret|api_key|apikey|authorization|cookie|session|credential|user|username|uid|login)\b(\s*[=:]\s*|\s+for\s+|\s+is\s+)([^\s,;)]+)",
            r"\1=[REDACTED]",
            text,
        )
        text = re.sub(r"['\"]?[^'\"\s@()]+['\"]?@['\"]?[^'\"\s()]+['\"]?", "[REDACTED_IDENTITY]", text)
        text = " ".join(text.split())[:260]
        if not text:
            return default
        prefix = f"MySQL {code} / {type(target).__name__}" if code and target is not exc else (
            f"MySQL {code}" if code else type(target).__name__
        )
        return f"{prefix}: {text}" if prefix not in text else text

    @classmethod
    def _mapped_tls_error(cls, exc, *, code=None, detail="", original=None, redact_values=()):
        sanitized = cls._sanitize_tls_detail(exc, redact_values=redact_values)
        if any(term in detail for term in ("doesn't support", "does not support", "not support")):
            return OTRSTLSHandshakeError(
                safe_message=(
                    "O servidor MySQL do OTRS não suporta conexões TLS. "
                    "Habilite TLS no servidor MySQL ou utilize túnel local em 127.0.0.1."
                ),
                error_code="server_tls_unsupported",
                technical_detail=sanitized or "O servidor MySQL não anunciou suporte a SSL/TLS (CR_SSL_CONNECTION_ERROR 2026).",
            )
        if (
            isinstance(exc, ssl.SSLCertVerificationError)
            or isinstance(original, ssl.SSLCertVerificationError)
            or any(term in detail for term in (
                "certificate_verify_failed", "certificate verify failed", "self-signed",
                "self signed", "unable to get local issuer", "unknown ca",
                "certificate has expired", "hostname mismatch", "ip address mismatch",
                "doesn't match", "certificate", "x509",
            ))
        ):
            return OTRSTLSCertificateError(
                error_code="certificate_verify_failed",
                technical_detail=sanitized or "Falha na verificação do certificado X.509 apresentado pelo servidor OTRS.",
            )
        return OTRSTLSHandshakeError(
            error_code="tls_handshake_failed",
            technical_detail=sanitized or "Erro de protocolo ou handshake SSL/TLS ao conectar ao OTRS.",
        )

    @classmethod
    def _mapped_error(cls, exc, *, redact_values=()):
        code = cls._error_code(exc)
        original = getattr(exc, "original_exception", None) or getattr(exc, "__cause__", None)
        detail_parts = [str(part) for part in getattr(exc, "args", ())]
        if original is not None:
            detail_parts.extend(str(part) for part in getattr(original, "args", ()))
            detail_parts.append(str(original))
        detail = " ".join(detail_parts).casefold()
        if code in {1045, 1698, 1130}:
            return OTRSAuthenticationError()
        if code == 1049:
            return OTRSDatabaseNotFoundError()
        if code == 2005 or any(term in detail for term in (
            "name or service not known", "getaddrinfo failed", "no route to host",
            "network is unreachable", "temporary failure in name resolution",
        )):
            return OTRSHostUnreachableError()
        if code in {2002, 2003} and any(term in detail for term in (
            "connection refused", "winerror 10061", "errno 111",
        )):
            return OTRSConnectionRefusedError()
        if code in {3024, 1969}:
            return OTRSTimeoutError()
        if code in {2002, 2003} and any(term in detail for term in (
            "timed out", "timeout", "winerror 10060", "errno 110",
        )):
            return OTRSTimeoutError()
        if (
            code == 2026
            or isinstance(exc, ssl.SSLError)
            or isinstance(original, ssl.SSLError)
            or any(term in detail for term in ("ssl", "tls", "certificate", "x509", "handshake"))
        ):
            return cls._mapped_tls_error(
                exc, code=code, detail=detail, original=original, redact_values=redact_values,
            )
        if code in {2002, 2003}:
            return OTRSHostUnreachableError()
        if code in {2006, 2013}:
            return OTRSUnavailableError()
        if isinstance(exc, TimeoutError) or any(term in detail for term in ("timed out", "timeout")):
            return OTRSTimeoutError()
        return OTRSDriverError()
