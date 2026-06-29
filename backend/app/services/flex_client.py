from collections.abc import Callable
import time
from ssl import SSLError
from xml.etree import ElementTree as ET
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import httpx


TRANSIENT_SEND_REQUEST_ERROR_CODES = {"1004"}


class FlexStatementPendingError(RuntimeError):
    """Raised when IBKR accepted the Flex request but the statement is not ready."""


class FlexStatementClient:
    """IBKR Flex Web Service client.

    TLS/network flakiness (especially through VPNs or regional routing) can trigger
    intermittent SSL EOF errors. We retry a few times with backoff for transport errors.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService",
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_connections=10),
            http2=False,
            follow_redirects=True,
        )

    def _request_get(self, *, path: str, params: dict[str, str]) -> httpx.Response:
        last_error: Exception | None = None
        url = f"{self._base_url}/{path}"
        for attempt in range(4):
            try:
                return self._client.get(url, params=params)
            except (httpx.TransportError, SSLError, OSError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(0.4 * (2**attempt))
        # Fallback to urllib for environments where httpx TLS handshake is flaky.
        try:
            query = urlencode(params)
            with urlopen(Request(f"{url}?{query}", method="GET"), timeout=15) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", 200)
            return httpx.Response(status_code=status, text=body)
        except Exception as exc:  # pragma: no cover - runtime network fallback
            last_error = exc
        assert last_error is not None
        raise RuntimeError(
            "flex network error (check VPN/proxy/firewall or try again later): "
            f"{last_error!s}"
        ) from last_error

    def request_reference_code(self, *, token: str, query_id: str) -> str:
        response = self._request_get(path="SendRequest", params={"t": token, "q": query_id, "v": "3"})
        if response.status_code >= 400:
            raise RuntimeError(f"flex send request failed: {response.status_code}")
        reference_code = _extract_xml_field(response.text, "ReferenceCode")
        if not reference_code:
            error_code = _extract_xml_field(response.text, "ErrorCode")
            flex_error = _format_flex_error(response.text, operation="send request")
            if flex_error:
                if error_code in TRANSIENT_SEND_REQUEST_ERROR_CODES:
                    raise FlexStatementPendingError(flex_error)
                raise RuntimeError(flex_error)
            status = _extract_xml_field(response.text, "Status")
            suffix = f" (status={status})" if status else ""
            raise RuntimeError(f"flex send request missing reference code{suffix}")
        return reference_code

    def download_statement(self, *, token: str, reference_code: str) -> str:
        response = self._request_get(
            path="GetStatement",
            params={"t": token, "q": reference_code, "v": "3"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"flex get statement failed: {response.status_code}")
        return response.text

    def fetch_statement_xml(
        self,
        *,
        token: str,
        query_id: str,
        max_attempts: int = 5,
        poll_interval_seconds: float = 5.0,
        sleeper: Callable[[float], object] | None = None,
    ) -> str:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than 0")
        sleep_fn = sleeper or time.sleep

        reference_code = ""
        last_pending_error: FlexStatementPendingError | None = None
        for attempt in range(max_attempts):
            try:
                reference_code = self.request_reference_code(token=token, query_id=query_id)
                break
            except FlexStatementPendingError as exc:
                last_pending_error = exc
                if attempt < max_attempts - 1:
                    sleep_fn(poll_interval_seconds)
        else:
            assert last_pending_error is not None
            raise RuntimeError(str(last_pending_error)) from last_pending_error

        for attempt in range(max_attempts):
            statement = self.download_statement(token=token, reference_code=reference_code)
            if _looks_like_ready_statement(statement):
                return statement
            if attempt < max_attempts - 1:
                sleep_fn(poll_interval_seconds)
        raise RuntimeError("flex statement not ready before max attempts")


def _extract_xml_field(xml_text: str, tag: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    node = _find_xml_node(root, tag)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _find_xml_node(root: ET.Element, tag: str) -> ET.Element | None:
    for node in root.iter():
        if _local_xml_name(node.tag) == tag:
            return node
    return None


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _format_flex_error(xml_text: str, *, operation: str) -> str | None:
    status = _extract_xml_field(xml_text, "Status")
    error_code = _extract_xml_field(xml_text, "ErrorCode")
    error_message = _extract_xml_field(xml_text, "ErrorMessage")
    if not error_code and not error_message and (status or "").lower() != "fail":
        return None
    parts = [f"IBKR Flex {operation} failed"]
    if error_code:
        parts.append(f" [{error_code}]")
    if error_message:
        parts.append(f": {error_message}")
    elif status:
        parts.append(f" (status={status})")
    return "".join(parts)


def _looks_like_ready_statement(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    return _local_xml_name(root.tag) == "FlexQueryResponse"
