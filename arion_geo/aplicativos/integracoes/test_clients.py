import json
import ssl
from unittest import TestCase
from unittest.mock import Mock

from aplicativos.integracoes.clients.cmdb import CMDBClient
from aplicativos.integracoes.clients.factory import normalize_otrs_config
from aplicativos.integracoes.clients.otrs import OTRSReadOnlyClient
from aplicativos.integracoes.forms import CMDBConfigurationForm, OTRSConfigurationForm
from aplicativos.integracoes.services.errors import (
    CMDBAuthenticationError,
    CMDBTimeoutError,
    IntegrationNotConfigured,
    OTRSAuthenticationError,
    OTRSConnectionRefusedError,
    OTRSCredentialInvalidError,
    OTRSDatabaseError,
    OTRSDatabaseNotFoundError,
    OTRSDriverError,
    OTRSHostUnreachableError,
    OTRSTLSCertificateError,
    OTRSTLSError,
    OTRSTLSHandshakeError,
    OTRSTLSRequiredError,
    OTRSTimeoutError,
)


class FakeHTTPResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body[:size]


class CMDBClientTests(TestCase):
    def test_search_posts_genericinterface_config_item_payload_and_a_bounded_limit(self):
        opener = Mock()
        opener.open.return_value = FakeHTTPResponse({"ConfigItem": [{"ConfigItemID": "4", "Name": "Link"}]})
        client = CMDBClient(base_url="https://cmdb.example/Webservice/", username="reader", password="private", opener=opener)

        result = client.search_items(class_name="Link", number="L-1", limit=999)

        request, = opener.open.call_args.args
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {"ConfigItem": {"Class": "Link", "Number": "L-1", "Limit": 100}, "UserLogin": "reader", "Password": "private"})
        self.assertNotIn("Authorization", request.headers)
        self.assertTrue(request.full_url.endswith("Webservice/CMDB/ConfigItemSearch"))
        self.assertEqual(result[0]["ConfigItemID"], "4")

    def test_get_splits_requested_ids_into_configured_batches(self):
        opener = Mock()
        opener.open.side_effect = [FakeHTTPResponse({"Items": [{"ConfigItemID": "1"}]}), FakeHTTPResponse({"Items": [{"ConfigItemID": "2"}]})]
        client = CMDBClient(base_url="https://cmdb.example/", username="reader", password="private", batch_size=1, opener=opener)

        result = client.get_items(["1", "2"])

        self.assertEqual(len(result), 2)
        self.assertEqual([json.loads(call.args[0].data.decode())["ConfigItemID"] for call in opener.open.call_args_list], ["1", "2"])

    def test_base_url_with_or_without_webservice_suffix_produces_correct_search_and_get_urls(self):
        for base_url in ("https://cmdb.example/otrs/nph-genericinterface.pl/Webservice", "https://cmdb.example/otrs/nph-genericinterface.pl/Webservice/"):
            with self.subTest(base_url=base_url):
                opener = Mock()
                opener.open.side_effect = [FakeHTTPResponse({"ConfigItemIDs": [4633]}), FakeHTTPResponse({"ConfigItem": [{"ConfigItemID": "4633"}]})]
                client = CMDBClient(base_url=base_url, username="configured-user", password="private", opener=opener)
                client.search_items(class_name="Links de internet", limit=1)
                search_request, get_request = [call.args[0] for call in opener.open.call_args_list]
                self.assertEqual(search_request.full_url, "https://cmdb.example/otrs/nph-genericinterface.pl/Webservice/CMDB/ConfigItemSearch")
                self.assertEqual(get_request.full_url, "https://cmdb.example/otrs/nph-genericinterface.pl/Webservice/CMDB/ConfigItemGet")
                search_payload = json.loads(search_request.data.decode())
                self.assertEqual(search_payload["ConfigItem"], {"Class": "Links de internet", "Limit": 1})
                self.assertEqual(search_payload["UserLogin"], "configured-user")
                self.assertEqual(json.loads(get_request.data.decode())["ConfigItemID"], "4633")

    def test_connection_accepts_nonempty_and_empty_config_item_ids_without_get(self):
        for ids in ([4633], []):
            with self.subTest(ids=ids):
                opener = Mock()
                opener.open.return_value = FakeHTTPResponse({"ConfigItemIDs": ids})
                client = CMDBClient(base_url="https://cmdb.example/Webservice/", username="reader", password="private", opener=opener)
                self.assertTrue(client.test_connection())
                self.assertEqual(opener.open.call_count, 1)
                payload = json.loads(opener.open.call_args.args[0].data.decode())
                self.assertEqual(payload["ConfigItem"], {"Class": "Links de internet", "Limit": 1})

    def test_read_only_study_search_supports_large_limit_and_omitted_limit(self):
        opener = Mock()
        opener.open.side_effect = [FakeHTTPResponse({"ConfigItemIDs": ["1"]}), FakeHTTPResponse({"ConfigItemIDs": ["2"]})]
        client = CMDBClient(base_url="https://cmdb.example/", username="reader", password="private", opener=opener)

        client.search_item_ids(class_name="Links de internet", limit=5000)
        client.search_item_ids(class_name="Links de internet", limit=None)

        payloads = [json.loads(call.args[0].data.decode("utf-8"))["ConfigItem"] for call in opener.open.call_args_list]
        self.assertEqual(payloads, [
            {"Class": "Links de internet", "Limit": 5000},
            {"Class": "Links de internet"},
        ])

    def test_search_resolves_generic_interface_ids_through_config_item_get(self):
        opener = Mock()
        opener.open.side_effect = [
            FakeHTTPResponse({"Success": 1, "Data": {"ConfigItemID": ["11", "12"]}}),
            FakeHTTPResponse({"Success": 1, "Data": {"ConfigItem": [{"ConfigItemID": "11", "Name": "Link A"}, {"ConfigItemID": "12", "Name": "Link B"}]}}),
        ]
        client = CMDBClient(base_url="https://cmdb.example/", username="reader", password="private", batch_size=50, opener=opener)
        result = client.search_items(limit=2)
        self.assertEqual([item["Name"] for item in result], ["Link A", "Link B"])
        self.assertTrue(opener.open.call_args_list[0].args[0].full_url.endswith("ConfigItemSearch"))
        self.assertTrue(opener.open.call_args_list[1].args[0].full_url.endswith("ConfigItemGet"))

    def test_http_200_generic_interface_authentication_error_is_not_reported_as_empty_search(self):
        opener = Mock()
        opener.open.return_value = FakeHTTPResponse({"Success": 0, "Error": {"ErrorCode": "ConfigItemSearch.AuthFail", "ErrorMessage": "private provider details"}})
        client = CMDBClient(base_url="https://cmdb.example/", username="reader", password="private", opener=opener)
        with self.assertRaises(CMDBAuthenticationError) as caught:
            client.search_items()
        self.assertEqual(caught.exception.error_code, "ConfigItemSearch.AuthFail")
        self.assertNotIn("private provider details", caught.exception.safe_message)

    def test_authentication_and_timeout_errors_are_safe_domain_errors(self):
        import urllib.error

        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://cmdb.example/", 401, "secret response", {}, None)
        client = CMDBClient(base_url="https://cmdb.example/", username="reader", password="private", opener=opener)
        with self.assertRaises(CMDBAuthenticationError):
            client.test_connection()
        opener.open.side_effect = TimeoutError("private timeout details")
        with self.assertRaises(CMDBTimeoutError):
            client.test_connection()

    def test_sanitizer_keeps_new_public_attributes_but_denies_sensitive_names(self):
        result = CMDBClient.sanitize_item({
            "ConfigItemID": "7", "Number": "L-2", "Name": "Metro", "Class": "Link",
            "Password": "top secret", "PortalPassword": "top secret", "UsuarioPortalOperadora": "portal-user",
            "CIXMLData": {"Designacao": "Circuito A", "ISP": "Operadora", "CNPJ": "123", "Equipamento": "router", "SenhaPPOE": "top secret", "UsuarioPortalOperadora": "portal-user", "RandomField": "hidden", "SessionCookie": "top secret"},
        })
        self.assertNotIn("Password", result)
        self.assertNotIn("UsuarioPortalOperadora", result)
        self.assertEqual(result["CIXMLData"], {"Designacao": "Circuito A", "ISP": "Operadora", "CNPJ": "123", "Equipamento": "router", "RandomField": "hidden"})
        self.assertNotIn("top secret", json.dumps(result))
        self.assertNotIn("portal-user", json.dumps(result))

    def test_cmdb_client_and_form_reject_non_https_urls(self):
        with self.assertRaises(IntegrationNotConfigured):
            CMDBClient(base_url="http://cmdb.example/Webservice/", username="reader", password="private", opener=Mock())
        form = CMDBConfigurationForm(data={
            "base_url": "http://cmdb.example/Webservice",
            "username": "reader",
            "password": "private",
            "timeout": 15,
            "verify_tls": True,
            "batch_size": 50,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("base_url", form.errors)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        self.query, self.params = query, params
        self.connection.queries.append((query, params))

    def fetchone(self):
        if "DATABASE()" in self.query:
            return {"database_name": "otrs", "server_version": "MariaDB 10.6", "server_time": "2026-10-24 12:00:00"}
        return {"connection_ok": 1}

    def fetchall(self):
        if "SCHEMATA" in self.query:
            return [{"SCHEMA_NAME": "otrs"}]
        if "information_schema.TABLES" in self.query:
            return [{"TABLE_NAME": "users", "TABLE_TYPE": "BASE TABLE", "TABLE_ROWS": 12}]
        if "information_schema.COLUMNS" in self.query:
            return [{"COLUMN_NAME": "pw", "COLUMN_TYPE": "varchar(64)", "IS_NULLABLE": "NO", "COLUMN_KEY": "", "COLUMN_DEFAULT": None, "EXTRA": ""}]
        if "information_schema.STATISTICS" in self.query:
            return [{"INDEX_NAME": "PRIMARY", "NON_UNIQUE": 0, "SEQ_IN_INDEX": 1, "COLUMN_NAME": "id", "INDEX_TYPE": "BTREE"}]
        if "KEY_COLUMN_USAGE" in self.query:
            return []
        if self.query.startswith("SELECT * FROM"):
            return [{"id": 1, "pw": "never expose", "token": "also private", "login": "reader"}]
        return []


class FakeConnection:
    def __init__(self):
        self.queries = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class OTRSReadOnlyClientTests(TestCase):
    def setUp(self):
        self.connections = []

        def factory():
            connection = FakeConnection()
            self.connections.append(connection)
            return connection

        self.client = OTRSReadOnlyClient(host="db.example", username="reader", password="private", database="otrs", connection_factory=factory)

    def test_connection_and_schema_operations_use_only_read_only_or_select_statements(self):
        self.assertTrue(self.client.test_connection())
        tables = self.client.list_tables()
        columns = self.client.get_table_schema("users")
        indexes = self.client.get_table_indexes("users")
        foreign_keys = self.client.get_foreign_keys("users")
        self.assertEqual(tables[0]["name"], "users")
        self.assertEqual(columns[0]["column"], "pw")
        self.assertEqual(indexes[0]["name"], "PRIMARY")
        self.assertEqual(foreign_keys, [])
        all_queries = [query.upper().strip() for connection in self.connections for query, _ in connection.queries]
        self.assertTrue(all(query.startswith(("SELECT", "SET SESSION TRANSACTION READ ONLY")) for query in all_queries))
        self.assertTrue(all(connection.closed for connection in self.connections))

    def test_connection_checks_selected_database_and_server_metadata(self):
        self.assertTrue(self.client.test_connection())
        query = self.connections[0].queries[-1][0]
        self.assertIn("DATABASE()", query)
        self.assertIn("VERSION()", query)
        self.assertIn("NOW()", query)

        self.client.database = "another_database"
        with self.assertRaises(OTRSDatabaseNotFoundError):
            self.client.test_connection()

    def test_driver_errors_have_specific_safe_categories(self):
        class DriverFailure(Exception):
            pass

        cases = (
            ((1045, "Access denied; password=never-show"), OTRSAuthenticationError),
            ((1049, "Unknown database otrs"), OTRSDatabaseNotFoundError),
            ((2003, "Can't connect [WinError 10061] Connection refused"), OTRSConnectionRefusedError),
            ((2003, "Can't connect [WinError 10060] timed out"), OTRSTimeoutError),
            ((2005, "Unknown MySQL server host"), OTRSHostUnreachableError),
            ((3024, "Query execution was interrupted"), OTRSTimeoutError),
            ((7777, "unexpected driver failure password=never-show"), OTRSDriverError),
        )
        for args, expected in cases:
            with self.subTest(code=args[0]):
                error = OTRSReadOnlyClient._mapped_error(DriverFailure(*args))
                self.assertIsInstance(error, expected)
                self.assertNotIn("never-show", error.safe_message)

    def test_password_must_be_decrypted_plaintext_string_without_line_breaks(self):
        for password in (b"encoded-bytes", "encoded\nvalue"):
            with self.subTest(password_type=type(password).__name__):
                with self.assertRaises(OTRSCredentialInvalidError) as caught:
                    OTRSReadOnlyClient(host="db.example", username="reader", password=password)
                self.assertNotIn("encoded", str(caught.exception))

    def test_sample_is_limited_and_sensitive_columns_are_masked(self):
        result = self.client.get_sample_rows("users", page_size=500)
        self.assertEqual(result[0]["pw"], "••••••")
        self.assertEqual(result[0]["token"], "••••••")
        self.assertEqual(result[0]["login"], "reader")
        sample_query = [query for connection in self.connections for query, _ in connection.queries if query.startswith("SELECT * FROM")][0]
        self.assertIn("LIMIT %s OFFSET %s", sample_query)
        sample_params = [params for connection in self.connections for query, params in connection.queries if query.startswith("SELECT * FROM")][0]
        self.assertEqual(sample_params, (50, 0))

    def test_sample_blocks_customer_user_and_article_tables_before_reading_rows(self):
        from unittest.mock import patch

        for table in ("customer_user", "article", "article_data_mime"):
            with self.subTest(table=table), patch.object(self.client, "_validated_table", return_value=table):
                with self.assertRaises(OTRSDatabaseError):
                    self.client.get_sample_rows(table)
        self.assertFalse(any(query.startswith("SELECT * FROM") for connection in self.connections for query, _ in connection.queries))

    def test_select_query_blocks_customer_and_article_content_fields(self):
        for query in (
            "SELECT customer_user FROM ticket",
            "SELECT a_body FROM article_data_mime",
            "SELECT internal_note FROM ticket_history",
        ):
            with self.subTest(query_name=query.split()[1]), self.assertRaises(OTRSDatabaseError):
                self.client.execute_select(query)
        self.assertFalse(self.connections)

    def test_sample_masks_customer_user_article_body_and_internal_note_columns(self):
        result = OTRSReadOnlyClient._sanitize_row({
            "customer_user": "customer@example.invalid",
            "article_id": 12,
            "a_body": "private OTRS article",
            "internal_note": "private note",
            "note_body": "private note body",
            "login": "reader",
        })
        for key in ("customer_user", "article_id", "a_body", "internal_note", "note_body"):
            self.assertEqual(result[key], "••••••")
        self.assertEqual(result["login"], "reader")
        self.assertNotIn("customer@example.invalid", str(result))
        self.assertNotIn("private OTRS article", str(result))

    def test_table_identifier_must_be_discovered_and_supported(self):
        with self.assertRaises(OTRSDatabaseError):
            self.client.get_sample_rows("users; DROP TABLE users")

    def test_legacy_null_and_missing_tls_options_normalize_safely(self):
        legacy_config = {
            "host": "200.174.198.133",
            "port": 3306,
            "database": "",
            "timeout": 10,
            "use_tls": None,
            "ssl_ca": None,
            "ssl_verify": None,
        }
        normalized = normalize_otrs_config(legacy_config)
        self.assertIsNone(normalized["use_tls"])
        self.assertEqual(normalized["ssl_ca"], "")
        self.assertIs(normalized["ssl_verify"], True)
        self.assertIs(normalized["allow_insecure_remote"], False)

        missing_config = normalize_otrs_config({"host": "200.174.198.133"})
        self.assertIsNone(missing_config["use_tls"])
        self.assertEqual(missing_config["ssl_ca"], "")
        self.assertIs(missing_config["ssl_verify"], True)
        self.assertIs(missing_config["allow_insecure_remote"], False)

        explicit_true = normalize_otrs_config({"host": "200.174.198.133", "ssl_verify": True})
        self.assertIs(explicit_true["ssl_verify"], True)

        client_from_nulls = OTRSReadOnlyClient(
            host="200.174.198.133",
            username="reader",
            password="private",
            use_tls=None,
            ssl_ca=None,
            ssl_verify=None,
            allow_insecure_remote=None,
        )
        self.assertTrue(client_from_nulls.use_tls)
        self.assertTrue(client_from_nulls.ssl_verify)
        self.assertFalse(client_from_nulls.allow_insecure_remote)
        self.assertEqual(client_from_nulls.ssl_ca, "")
        context = client_from_nulls._build_ssl_context()
        self.assertIsNotNone(context)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_remote_host_requires_tls_fail_closed_while_loopback_allows_local_mode(self):
        remote_insecure_tls = OTRSReadOnlyClient(
            host="200.174.198.133", username="reader", password="private", use_tls=False
        )
        self.assertFalse(remote_insecure_tls.allow_insecure_remote)
        with self.assertRaises(OTRSTLSRequiredError) as caught_tls:
            remote_insecure_tls._build_ssl_context()
        self.assertEqual(caught_tls.exception.error_code, "tls_policy_required")

        remote_insecure_verify = OTRSReadOnlyClient(
            host="200.174.198.133", username="reader", password="private", use_tls=True, ssl_verify=False
        )
        with self.assertRaises(OTRSTLSRequiredError) as caught_verify:
            remote_insecure_verify._build_ssl_context()
        self.assertEqual(caught_verify.exception.error_code, "tls_policy_required")

        remote_tls_unverified_with_legacy_flag = OTRSReadOnlyClient(
            host="200.174.198.133",
            username="reader",
            password="private",
            use_tls=True,
            ssl_verify=False,
            allow_insecure_remote=True,
        )
        with self.assertRaises(OTRSTLSRequiredError):
            remote_tls_unverified_with_legacy_flag._build_ssl_context()

        remote_legacy_opt_in = OTRSReadOnlyClient(
            host="200.174.198.133",
            username="secret-user",
            password="secret-password",
            use_tls=False,
            allow_insecure_remote=True,
        )
        with self.assertLogs("aplicativos.integracoes.clients.otrs", level="WARNING") as captured_logs:
            self.assertIsNone(remote_legacy_opt_in._build_ssl_context())
        joined_logs = " ".join(captured_logs.output)
        self.assertIn(
            "OTRS remoto usando transporte MySQL sem TLS por configuração explícita de compatibilidade legada.",
            joined_logs,
        )
        self.assertNotIn("secret-user", joined_logs)
        self.assertNotIn("secret-password", joined_logs)

        remote_default = OTRSReadOnlyClient(
            host="200.174.198.133", username="reader", password="private"
        )
        self.assertTrue(remote_default.use_tls)
        self.assertTrue(remote_default.ssl_verify)
        self.assertFalse(remote_default.allow_insecure_remote)
        self.assertIsNotNone(remote_default._build_ssl_context())

        for loopback_host in ("127.0.0.1", "localhost"):
            with self.subTest(loopback_host=loopback_host):
                loopback_client = OTRSReadOnlyClient(
                    host=loopback_host, username="reader", password="private"
                )
                self.assertFalse(loopback_client.use_tls)
                self.assertIsNone(loopback_client._build_ssl_context())

        form_invalid = OTRSConfigurationForm(data={
            "host": "200.174.198.133", "port": 3306, "database": "otrs",
            "username": "reader", "password": "private", "timeout": 10, "use_tls": False,
        })
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("use_tls", form_invalid.errors)

        form_loopback = OTRSConfigurationForm(data={
            "host": "127.0.0.1", "port": 3306, "database": "otrs",
            "username": "reader", "password": "private", "timeout": 10, "use_tls": False,
        })
        self.assertTrue(form_loopback.is_valid())

    def test_tls_certificate_and_handshake_errors_are_distinct_from_policy_disabled_and_sanitized(self):
        class DriverFailure(Exception):
            pass

        cert_exc = ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1000)"
        )
        wrapped_cert_exc = DriverFailure(
            2003,
            "Can't connect to MySQL server on '200.174.198.133' "
            "([SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1000))",
        )
        wrapped_cert_exc.original_exception = cert_exc

        mapped_cert = OTRSReadOnlyClient._mapped_error(
            wrapped_cert_exc, redact_values=("secret-db-pass", "otrs-reader")
        )
        self.assertIsInstance(mapped_cert, OTRSTLSCertificateError)
        self.assertIsInstance(mapped_cert, OTRSTLSError)
        self.assertNotIsInstance(mapped_cert, OTRSTLSRequiredError)
        self.assertEqual(mapped_cert.error_code, "certificate_verify_failed")
        self.assertIn("CERTIFICATE_VERIFY_FAILED", mapped_cert.technical_detail)

        unsupported_exc = DriverFailure(2026, "SSL is required but the server doesn't support it")
        mapped_unsupported = OTRSReadOnlyClient._mapped_error(unsupported_exc)
        self.assertIsInstance(mapped_unsupported, OTRSTLSHandshakeError)
        self.assertIsInstance(mapped_unsupported, OTRSTLSError)
        self.assertNotIsInstance(mapped_unsupported, OTRSTLSRequiredError)
        self.assertEqual(mapped_unsupported.error_code, "server_tls_unsupported")
        self.assertIn("2026", mapped_unsupported.technical_detail)

        handshake_exc = ssl.SSLError(
            1,
            "[SSL: WRONG_VERSION_NUMBER] wrong version number "
            "user=otrs-reader password=secret-db-pass token=tok-123 "
            "mysql://otrs-reader:secret-db-pass@200.174.198.133:3306/otrs",
        )
        mapped_handshake = OTRSReadOnlyClient._mapped_error(
            handshake_exc, redact_values=("secret-db-pass", "otrs-reader")
        )
        self.assertIsInstance(mapped_handshake, OTRSTLSHandshakeError)
        self.assertNotIsInstance(mapped_handshake, OTRSTLSRequiredError)
        self.assertEqual(mapped_handshake.error_code, "tls_handshake_failed")
        summary = mapped_handshake.diagnostic_summary()
        self.assertIn("WRONG_VERSION_NUMBER", summary)
        for forbidden in ("secret-db-pass", "otrs-reader", "tok-123", "mysql://otrs-reader"):
            self.assertNotIn(forbidden, summary)
            self.assertNotIn(forbidden, mapped_handshake.technical_detail)

        missing_ca_client = OTRSReadOnlyClient(
            host="200.174.198.133",
            username="reader",
            password="private",
            ssl_ca="Z:/nonexistent/arion-otrs-ca.pem",
        )
        with self.assertRaises(OTRSTLSCertificateError) as caught_ca:
            missing_ca_client._build_ssl_context()
        self.assertNotIsInstance(caught_ca.exception, OTRSTLSRequiredError)
        self.assertEqual(caught_ca.exception.error_code, "tls_ca_not_found")
