"""FortiOS REST API async HTTP client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FortiOSError(Exception):
    """Raised on FortiOS API errors."""

    def __init__(
        self, message: str, status_code: int = 0, data: dict | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.data = data or {}


class FortiOSClient:
    """Async client for the FortiOS 7.6.6 REST API (v2).

    Supports all four API sections:
    - ``/api/v2/cmdb/``   — Configuration Management DB (read/write)
    - ``/api/v2/monitor/`` — Real-time operational data (mostly read-only)
    - ``/api/v2/log/``    — Log retrieval
    - ``/api/v2/service/`` — Service operations (security rating, sniffer …)

    Authentication is done via Bearer token passed in the ``Authorization``
    header as required by FortiOS 7.6.x.

    Writes are refused unless ``allow_writes`` is set, so a server pointed at
    production firewalls answers questions but cannot change them until that is
    turned on deliberately.

    Usage::

        async with FortiOSClient(host, token) as client:
            data = await client.cmdb_get("system/status")
    """

    _BASE = "/api/v2"

    def __init__(
        self,
        host: str,
        api_token: str,
        vdom: str = "root",
        verify_ssl: bool = False,
        timeout: float = 30.0,
        name: str | None = None,
        allow_writes: bool = False,
    ) -> None:
        self.host = host.rstrip("/")
        self.api_token = api_token
        # Inventory name, used only to identify the device in log lines.
        self.name = name or self.host
        # Read-only unless switched on: a server pointed at production
        # firewalls should not be able to change them by accident.
        self.allow_writes = allow_writes
        self.vdom = vdom
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "FortiOSClient":
        self._client = httpx.AsyncClient(
            base_url=self.host,
            headers=self._auth_headers(),
            verify=self.verify_ssl,
            timeout=self.timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _client_guard(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "FortiOSClient must be used as an async context manager."
            )
        return self._client

    def _vdom_params(
        self, extra: dict[str, Any] | None = None, vdom: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"vdom": vdom or self.vdom}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        return params

    async def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Issue one request, turning transport failures into FortiOSError.

        Connect timeouts, DNS failures and TLS errors surface as httpx
        exceptions whose str() is often empty (ConnectTimeout is the common
        case). Tools catch FortiOSError, so wrapping here gives every tool a
        readable message naming the host instead of a blank error — which
        matters most with a multi-device inventory, where one unreachable
        firewall is a normal condition rather than a fault.
        """
        # The verb is the classification: every read is a GET, and every write
        # or action is a POST, PUT or DELETE. Gating here rather than per tool
        # means a tool added later cannot slip through unclassified.
        if method != "GET" and not self.allow_writes:
            raise FortiOSError(
                f"Refusing {method} on device {self.name!r}: this server is "
                "running read-only. Set FORTIOS_ALLOW_WRITES=true to allow "
                "changes."
            )

        client = self._client_guard()
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or type(exc).__name__
            # Logged as well as raised: the caller gets the error in its return
            # value, but an operator watching the server needs to see which
            # firewall stopped answering without reading every tool response.
            logger.warning(
                "Device %r unreachable: %s %s — %s", self.name, method, url, detail
            )
            raise FortiOSError(f"Cannot reach {self.host}: {detail}") from exc
        return self._check_response(resp)

    def _check_response(self, resp: httpx.Response) -> dict[str, Any]:
        """Parse and validate a FortiOS JSON response."""
        try:
            body: dict[str, Any] = resp.json()
        except Exception as exc:
            raise FortiOSError(
                f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:300]}",
                resp.status_code,
            ) from exc

        http_status: int = body.get("http_status", resp.status_code)
        if resp.status_code >= 400 or http_status >= 400:
            status_msg = body.get("status", "error")
            error_msg = body.get("cli_error", body.get("http_method", str(body)))
            # 401/403 almost always mean the API token or its admin profile is
            # wrong, which is a configuration problem worth surfacing in the
            # server log. Other API errors (a missing object on delete, say) are
            # routine and stay at debug level.
            level = logging.WARNING if http_status in (401, 403) else logging.DEBUG
            logger.log(
                level,
                "Device %r API error %s on %s: %s",
                self.name,
                http_status,
                resp.request.url.path,
                status_msg,
            )
            raise FortiOSError(
                f"FortiOS API error {http_status}: {status_msg} — {error_msg}",
                http_status,
                body,
            )
        return body

    # ------------------------------------------------------------------
    # CMDB  (/api/v2/cmdb/…)
    # ------------------------------------------------------------------

    async def cmdb_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v2/cmdb/{path} — retrieve configuration."""
        url = f"{self._BASE}/cmdb/{path.lstrip('/')}"
        return await self._send("GET", url, params=self._vdom_params(params, vdom))

    async def cmdb_post(
        self,
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v2/cmdb/{path} — create a configuration object."""
        url = f"{self._BASE}/cmdb/{path.lstrip('/')}"
        return await self._send(
            "POST", url, json=body, params=self._vdom_params(params, vdom)
        )

    async def cmdb_put(
        self,
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """PUT /api/v2/cmdb/{path} — replace a configuration object."""
        url = f"{self._BASE}/cmdb/{path.lstrip('/')}"
        return await self._send(
            "PUT", url, json=body, params=self._vdom_params(params, vdom)
        )

    async def cmdb_delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """DELETE /api/v2/cmdb/{path} — remove a configuration object."""
        url = f"{self._BASE}/cmdb/{path.lstrip('/')}"
        return await self._send("DELETE", url, params=self._vdom_params(params, vdom))

    # ------------------------------------------------------------------
    # Monitor  (/api/v2/monitor/…)
    # ------------------------------------------------------------------

    async def monitor_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v2/monitor/{path} — retrieve operational/real-time data."""
        url = f"{self._BASE}/monitor/{path.lstrip('/')}"
        return await self._send("GET", url, params=self._vdom_params(params, vdom))

    async def monitor_post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v2/monitor/{path} — trigger a monitor action."""
        url = f"{self._BASE}/monitor/{path.lstrip('/')}"
        return await self._send(
            "POST", url, json=body or {}, params=self._vdom_params(params, vdom)
        )

    # ------------------------------------------------------------------
    # Log  (/api/v2/log/…)
    # ------------------------------------------------------------------

    async def log_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v2/log/{path} — retrieve log entries."""
        url = f"{self._BASE}/log/{path.lstrip('/')}"
        return await self._send("GET", url, params=self._vdom_params(params, vdom))

    async def log_post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v2/log/{path} — start a log search or action."""
        url = f"{self._BASE}/log/{path.lstrip('/')}"
        return await self._send(
            "POST", url, json=body or {}, params=self._vdom_params(params, vdom)
        )

    # ------------------------------------------------------------------
    # Service  (/api/v2/service/…)
    # ------------------------------------------------------------------

    async def service_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v2/service/{path}."""
        url = f"{self._BASE}/service/{path.lstrip('/')}"
        return await self._send("GET", url, params=self._vdom_params(params, vdom))

    async def service_post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        vdom: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v2/service/{path}."""
        url = f"{self._BASE}/service/{path.lstrip('/')}"
        return await self._send(
            "POST", url, json=body or {}, params=self._vdom_params(params, vdom)
        )
