"""Small synchronous client for the ServiceUp OTRS GenericInterface CMDB API."""
import json
import logging
import re
import socket
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

from aplicativos.integracoes.services.errors import (
    CMDBAuthenticationError,
    CMDBInvalidResponseError,
    CMDBTimeoutError,
    CMDBTLSFailure,
    CMDBUnavailableError,
    IntegrationNotConfigured,
)

logger = logging.getLogger(__name__)


class CMDBClient:
    SEARCH_PATH = "CMDB/ConfigItemSearch"
    GET_PATH = "CMDB/ConfigItemGet"
    MAX_LIMIT = 100
    MAX_STUDY_LIMIT = 5000
    PUBLIC_ITEM_FIELDS = (
        "ConfigItemID", "Number", "Name", "Class", "CurDeplState",
        "CurDeplStateType", "CurInciState", "CurInciStateType", "VersionID", "LastVersionID", "CIXMLData",
    )
    SENSITIVE_PARTS = ("password", "passwd", "pwd", "senha", "credential", "secret", "token", "auth", "cookie", "session", "apikey", "privatekey", "portalusername", "usuarioportal")

    def __init__(self, *, base_url, username, password, timeout=15, verify_tls=True, batch_size=50, opener=None):
        parsed = urlsplit((base_url or "").strip())
        if parsed.scheme.casefold() != "https" or not parsed.netloc or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise IntegrationNotConfigured()
        if not username or not password:
            raise IntegrationNotConfigured()
        self.base_url = base_url.rstrip("/")
        self._base_includes_webservice = urlsplit(self.base_url).path.rstrip("/").casefold().endswith("/webservice")
        self.username = username
        self.password = password
        self.timeout = max(1, min(int(timeout), 60))
        self.verify_tls = bool(verify_tls)
        self.batch_size = max(1, min(int(batch_size), 100))
        self.requests_count = 0
        if opener is None:
            if self.verify_tls:
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            else:
                # This choice is explicit in the settings form; TLS verification is otherwise on.
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = opener

    def test_connection(self):
        response = self._post(self.SEARCH_PATH, {"ConfigItem": {"Class": "Links de internet", "Limit": 1}})
        if not self._contains_item_ids(response):
            raise CMDBInvalidResponseError()
        return True

    def search_items(self, *, class_name="", number="", name="", cixml_data="", limit=25):
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        filters = {"Class": class_name, "Number": number, "Name": name}
        config_item = {key: str(value).strip() for key, value in filters.items() if str(value or "").strip()}
        if str(cixml_data or "").strip():
            try:
                parsed_xml_filter = json.loads(cixml_data)
                config_item["CIXMLData"] = parsed_xml_filter if isinstance(parsed_xml_filter, dict) else cixml_data.strip()
            except (TypeError, json.JSONDecodeError):
                config_item["CIXMLData"] = cixml_data.strip()
        config_item["Limit"] = limit
        response = self._post(self.SEARCH_PATH, {"ConfigItem": config_item})
        items = self._items_from_response(response)
        if items:
            return items[:limit]
        identifiers = self._item_ids_from_response(response)
        return self.get_items(identifiers[:limit]) if identifiers else []

    def search_item_ids(self, *, class_name="", limit=100):
        """Search IDs only for controlled, read-only inventory studies."""
        config_item = {"Class": str(class_name).strip()}
        if limit is not None:
            config_item["Limit"] = max(1, min(int(limit), self.MAX_STUDY_LIMIT))
        response = self._post(self.SEARCH_PATH, {"ConfigItem": config_item})
        if not self._contains_item_ids(response):
            raise CMDBInvalidResponseError()
        return self._item_ids_from_response(response)

    def get_items(self, config_item_ids):
        identifiers = []
        for value in config_item_ids:
            item = str(value).strip()
            if item and item not in identifiers:
                identifiers.append(item)
        if not identifiers:
            return []
        results = []
        for offset in range(0, len(identifiers), self.batch_size):
            batch = identifiers[offset:offset + self.batch_size]
            response = self._post(self.GET_PATH, {"ConfigItemID": ",".join(batch)})
            results.extend(self._items_from_response(response))
        return results

    def _post(self, endpoint, payload):
        self.requests_count += 1
        endpoint_prefix = "" if self._base_includes_webservice else "Webservice/"
        url = urljoin(self.base_url.rstrip("/") + "/", endpoint_prefix + endpoint)
        authenticated_payload = {**payload, "UserLogin": self.username, "Password": self.password}
        body = json.dumps(authenticated_payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise CMDBInvalidResponseError()
            parsed = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CMDBAuthenticationError(http_status=exc.code) from None
            if exc.code == 404:
                raise CMDBInvalidResponseError(http_status=404) from None
            if exc.code in (400, 422):
                raise CMDBInvalidResponseError(http_status=exc.code) from None
            raise CMDBUnavailableError(http_status=exc.code) from None
        except (TimeoutError, socket.timeout):
            raise CMDBTimeoutError() from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise CMDBTimeoutError() from None
            if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
                raise CMDBTLSFailure() from None
            raise CMDBUnavailableError() from None
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise CMDBInvalidResponseError() from None
        if not isinstance(parsed, (dict, list)):
            raise CMDBInvalidResponseError()
        self._raise_if_api_error(parsed)
        return parsed

    @classmethod
    def _items_from_response(cls, response):
        if isinstance(response, list):
            items = []
            for value in response:
                items.extend(cls._items_from_response(value))
            return items
        if not isinstance(response, dict):
            return []
        normalized = {cls._normalize_key(key): value for key, value in response.items()}
        if any(key in normalized for key in ("name", "number", "cixmldata", "curdeplstate", "curincistate", "versionid")):
            return [response]
        # GenericInterface returns operation values under Data; some mappings wrap
        # complete items in ConfigItem while ConfigItemSearch returns only IDs.
        containers = {"configitem", "configitems", "items", "item", "result", "data", "configitemsearch", "configitemget"}
        if "configitemid" in normalized and not isinstance(normalized["configitemid"], (dict, list, tuple)) and not containers.intersection(normalized):
            return [response]
        for key, value in response.items():
            if cls._normalize_key(key) in containers:
                found = cls._items_from_response(value)
                if found:
                    return found
        # Some mappings use the item ID as an object key.
        for value in response.values():
            if isinstance(value, (dict, list)):
                found = cls._items_from_response(value)
                if found:
                    return found
        return []

    @classmethod
    def _item_ids_from_response(cls, response):
        identifiers = []

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if cls._normalize_key(key) in {"configitemid", "configitemids"}:
                        candidates = item if isinstance(item, (list, tuple, set)) else [item]
                        for candidate in candidates:
                            if candidate is not None and not isinstance(candidate, (dict, list)):
                                values = str(candidate).split(",") if isinstance(candidate, str) else [candidate]
                                for value in values:
                                    identifier = str(value).strip()
                                    if identifier and identifier not in identifiers:
                                        identifiers.append(identifier)
                    else:
                        walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)

        walk(response)
        return identifiers

    @classmethod
    def _raise_if_api_error(cls, response):
        if not isinstance(response, dict):
            return
        error_parts = []
        error_codes = []

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = cls._normalize_key(key)
                    if normalized == "errorcode" and item:
                        code = str(item).strip()
                        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", code):
                            error_codes.append(code)
                        error_parts.append(code.casefold())
                    if normalized in {"error", "errorcode", "errormessage", "fault"} and item:
                        if isinstance(item, (dict, list)):
                            walk(item)
                        else:
                            error_parts.append(str(item).casefold())
                    elif normalized in {"success", "successful"} and str(item).casefold() in {"0", "false", "no"}:
                        error_parts.append("operation failed")
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(response)
        if not error_parts:
            return
        combined = " ".join(error_parts)
        safe_code = error_codes[0] if error_codes else ""
        if any(word in combined for word in ("auth", "login", "userlogin", "password", "credential")):
            raise CMDBAuthenticationError(error_code=safe_code, http_status=200)
        raise CMDBInvalidResponseError()

    @classmethod
    def _contains_item_ids(cls, response):
        if isinstance(response, dict):
            for key, value in response.items():
                if cls._normalize_key(key) in {"configitemids", "configitemid"}:
                    return isinstance(value, (list, tuple, str, int))
                if isinstance(value, (dict, list)) and cls._contains_item_ids(value):
                    return True
        elif isinstance(response, list):
            return any(cls._contains_item_ids(value) for value in response)
        return False

    @classmethod
    def sanitize_item(cls, item):
        """Return the fields used by the integration UI after denying sensitive names."""
        if not isinstance(item, dict):
            return {}
        normalized = {cls._normalize_key(key): value for key, value in item.items()}
        output = {}
        for key in cls.PUBLIC_ITEM_FIELDS[:-1]:
            value = normalized.get(cls._normalize_key(key))
            if value is not None and not cls._is_sensitive(key):
                output[key] = cls._display_value(value)
        attributes = cls._allowed_attributes(normalized.get("cixmldata"))
        if attributes:
            output["CIXMLData"] = attributes
        return output

    @classmethod
    def sanitize_items(cls, items):
        return [cls.sanitize_item(item) for item in items if isinstance(item, dict)]

    @classmethod
    def sanitize_study_items(cls, items):
        """Keep dynamic fields in memory for the study, replacing secrets with presence markers."""
        return [cls.sanitize_study_item(item) for item in items if isinstance(item, dict)]

    @classmethod
    def sanitize_study_item(cls, item):
        if not isinstance(item, dict):
            return {}
        output = {}
        for key, value in item.items():
            if not key:
                continue
            if cls._is_sensitive(key):
                output[str(key)] = cls._protected_value(value)
            elif cls._normalize_key(key) == "cixmldata":
                attributes = cls._allowed_attributes(value, preserve_sensitive_presence=True)
                if attributes:
                    output["CIXMLData"] = attributes
            else:
                output[str(key)] = cls._sanitize_value(value)
        return output

    @classmethod
    def _allowed_attributes(cls, value, preserve_sensitive_presence=False):
        flattened = {}
        if isinstance(value, str) and value.strip().startswith("<"):
            try:
                root = ET.fromstring(value)
                for element in root.iter():
                    if list(element):
                        continue
                    if element.tag:
                        flattened[element.tag.split("}")[-1]] = (element.text or "").strip()
            except ET.ParseError:
                return {}
        elif isinstance(value, dict):
            def walk(node):
                if isinstance(node, dict):
                    for key, item in node.items():
                        if cls._is_sensitive(key):
                            if preserve_sensitive_presence:
                                flattened[str(key)] = cls._protected_value(item)
                        elif isinstance(item, (dict, list)):
                            walk(item)
                        else:
                            flattened[str(key)] = item
                elif isinstance(node, list):
                    for item in node:
                        walk(item)
            walk(value)
        else:
            return {}
        allowed = {}
        for key, item in flattened.items():
            if key and (preserve_sensitive_presence or not cls._is_sensitive(key)):
                allowed[key] = cls._protected_value(item) if preserve_sensitive_presence and cls._is_sensitive(key) else cls._display_value(item)
        return allowed

    @classmethod
    def _sanitize_value(cls, value, depth=0):
        if isinstance(value, dict):
            if depth >= 6:
                return "Dados estruturados"
            return {
                str(key): cls._protected_value(item) if cls._is_sensitive(key) else cls._sanitize_value(item, depth + 1)
                for key, item in value.items()
                if key
            }
        if isinstance(value, list):
            if depth >= 6:
                return "Dados estruturados"
            return [cls._sanitize_value(item, depth + 1) for item in value[:500]]
        if isinstance(value, str):
            return value[:1000]
        return value

    @classmethod
    def _protected_value(cls, value):
        if value is None or value == "" or value == [] or value == {}:
            return ""
        return "[PROTEGIDO]"

    @staticmethod
    def _normalize_key(value):
        return "".join(char.lower() for char in str(value) if char.isalnum())

    @classmethod
    def _is_sensitive(cls, key):
        normalized = cls._normalize_key(key)
        return any(part in normalized for part in cls.SENSITIVE_PARTS)

    @staticmethod
    def _display_value(value):
        if isinstance(value, (dict, list)):
            return "Dados estruturados"
        return str(value)[:1000]
