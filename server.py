"""FortiOS 7.6.6 MCP Server — Complete REST API implementation.

This server exposes the entire Fortinet FortiOS 7.6.6 REST API
(1536 endpoints across CMDB, Monitor, Log, and Service sections)
as Model Context Protocol tools.

Architecture:
- Generic CRUD tools (cover all 1536 endpoints directly)
- Specific typed tools (cover the most important 150+ operations)
- Async HTTP client with Bearer-token authentication

Configuration (environment variables or .env file):
    FORTIOS_HOST       — FortiGate URL (e.g. https://192.168.1.1)
    FORTIOS_API_TOKEN  — API Bearer token
    FORTIOS_VDOM       — VDOM (default: root)
    FORTIOS_VERIFY_SSL — true/false (default: false for self-signed)
    FORTIOS_TIMEOUT    — HTTP timeout in seconds (default: 30)

HTTP transport only (MCP_TRANSPORT=streamable-http):
    MCP_HOST           — bind address (default: 127.0.0.1)
    MCP_PORT           — bind port (default: 8000)
    MCP_AUTH_TOKEN     — bearer token required on every HTTP request.
                         Unset/empty leaves the endpoint UNAUTHENTICATED.
    MCP_ALLOWED_HOSTS  — extra Host header values accepted for DNS rebinding
                         protection, JSON array or comma-separated. Needed when
                         clients connect via an IP or reverse-proxy hostname,
                         e.g. ["10.211.112.12:8000"] or "mcp.example.com".
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from fortios_client import FortiOSClient

# ── Tool modules ──────────────────────────────────────────────────────
from tools import (
    generic,
    system,
    firewall,
    vpn,
    router,
    user,
    monitor,
    log,
    security,
    wireless,
)

# Load .env if present
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("fortios_mcp")


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


def _get_config() -> dict:
    return {
        "host": _required_env("FORTIOS_HOST"),
        "api_token": _required_env("FORTIOS_API_TOKEN"),
        "vdom": os.environ.get("FORTIOS_VDOM", "root").strip(),
        "verify_ssl": os.environ.get("FORTIOS_VERIFY_SSL", "false").lower()
        in ("1", "true", "yes"),
        "timeout": float(os.environ.get("FORTIOS_TIMEOUT", "30")),
    }


# ─────────────────────────────────────────────────────────────────────
# HTTP transport security (streamable-http only)
# ─────────────────────────────────────────────────────────────────────


class BearerAuthMiddleware:
    """Require `Authorization: Bearer <token>` on every HTTP request.

    Plain ASGI middleware rather than FastMCP's ``token_verifier``/``AuthSettings``
    pair: the latter publishes OAuth 2.1 protected-resource metadata and points
    401 responses at OAuth discovery, which makes clients attempt an OAuth flow
    instead of simply sending the shared token.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header = Headers(scope=scope).get("authorization", "")
        scheme, _, credentials = header.partition(" ")

        # compare_digest keeps the check constant-time; str.strip() first so a
        # trailing newline in the client config is not treated as a mismatch.
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            credentials.strip(), self._token
        ):
            logger.warning(
                "Rejected unauthenticated request: %s %s",
                scope.get("method", "?"),
                scope.get("path", "?"),
            )
            response = JSONResponse(
                {"error": "unauthorized", "detail": "Valid bearer token required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _parse_allowed_hosts(raw: str) -> list[str]:
    """Parse MCP_ALLOWED_HOSTS from a JSON array or a comma-separated string."""
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(
                f"MCP_ALLOWED_HOSTS is not valid JSON: {raw!r}. "
                'Use ["host:port"] or a comma-separated list.'
            ) from None
        if not isinstance(parsed, list):
            raise ValueError("MCP_ALLOWED_HOSTS JSON must be an array of strings.")
        return [str(h).strip() for h in parsed if str(h).strip()]
    return [h.strip() for h in raw.split(",") if h.strip()]


def _transport_security() -> TransportSecuritySettings | None:
    """Build DNS rebinding protection settings from MCP_ALLOWED_HOSTS.

    FastMCP auto-enables this protection only when bound to loopback, so a
    container bound to 0.0.0.0 has it off unless configured here. Returning None
    keeps that permissive default.
    """
    hosts = _parse_allowed_hosts(os.environ.get("MCP_ALLOWED_HOSTS", ""))
    if not hosts:
        return None

    # Loopback stays allowed regardless, so same-host clients, curl checks and a
    # reverse proxy on the Docker host keep working after the list is narrowed.
    loopback = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_hosts = hosts + [h for h in loopback if h not in hosts]

    # Browser-based clients send an Origin header; without matching entries the
    # middleware would reject them with 403 even though the Host header is fine.
    allowed_origins = [
        f"{scheme}://{host}" for host in allowed_hosts for scheme in ("http", "https")
    ]

    logger.info("DNS rebinding protection enabled for hosts: %s", allowed_hosts)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


# ─────────────────────────────────────────────────────────────────────
# Lifespan — shared FortiOS client
# ─────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncGenerator[dict, None]:
    """Create and manage the FortiOS API client lifecycle."""
    cfg = _get_config()
    logger.info(
        "Connecting to FortiGate %s (vdom=%s, ssl-verify=%s)",
        cfg["host"],
        cfg["vdom"],
        cfg["verify_ssl"],
    )
    client = FortiOSClient(
        host=cfg["host"],
        api_token=cfg["api_token"],
        vdom=cfg["vdom"],
        verify_ssl=cfg["verify_ssl"],
        timeout=cfg["timeout"],
    )
    async with client:
        logger.info("FortiOS client initialized.")
        yield {"client": client}
    logger.info("FortiOS client closed.")


# ─────────────────────────────────────────────────────────────────────
# FastMCP application
# ─────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="FortiOS MCP Server",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8000")),
    instructions=(
        "You are connected to a Fortinet FortiGate running FortiOS 7.6.6. "
        "This server exposes the complete FortiOS REST API as MCP tools. "
        "\n\n"
        "Tool categories:\n"
        "- **Generic**: cmdb_list/get/create/update/delete, monitor_get/action, log_get, service_call\n"
        "    → These cover ALL 1536 FortiOS API endpoints directly.\n"
        "- **System**: interfaces, DNS, NTP, admins, DHCP, SNMP, certificates, VDOMs\n"
        "- **Firewall**: policies, addresses, services, VIPs, IP pools, schedules, sessions\n"
        "- **VPN**: IPsec Phase 1/2, SSL VPN portals/settings, tunnel control, certificates\n"
        "- **Router**: static routes, OSPF, BGP, RIP, prefix lists, route maps, SD-WAN\n"
        "- **User**: local users, groups, RADIUS, LDAP, TACACS+, SAML, session management\n"
        "- **Monitor**: ARP, FortiView, license, endpoint, IPS, switch controller, config backup\n"
        "- **Log**: traffic/event/virus/webfilter/IPS logs, FortiAnalyzer config\n"
        "- **Security**: IPS, AV, webfilter, app control, DLP, email filter, DNS filter, WAF, ZTNA\n"
        "- **Wireless**: APs, SSIDs, Hotspot 2.0, connected clients, rogue AP detection\n"
        "\n"
        "Always use specific typed tools when available. "
        "Fall back to generic cmdb_list/cmdb_get/monitor_get for unlisted resources. "
        "For destructive operations (delete, policy changes), confirm with the user first."
    ),
    lifespan=lifespan,
    transport_security=_transport_security(),
)

# ─────────────────────────────────────────────────────────────────────
# Register all tool modules
# ─────────────────────────────────────────────────────────────────────

generic.register(mcp)
system.register(mcp)
firewall.register(mcp)
vpn.register(mcp)
router.register(mcp)
user.register(mcp)
monitor.register(mcp)
log.register(mcp)
security.register(mcp)
wireless.register(mcp)

logger.info("All 10 tool modules registered (204+ tools).")


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────


def _run_http() -> None:
    """Serve streamable-http, optionally behind bearer authentication.

    Replaces ``mcp.run(transport="streamable-http")`` (which builds the same
    Starlette app and runs it under uvicorn) so the ASGI app can be wrapped
    before it is served.
    """
    import uvicorn

    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8000"))
    logger.info("Starting FortiOS MCP Server on %s:%d (HTTP)", host, port)

    app = mcp.streamable_http_app()

    auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if auth_token:
        app = BearerAuthMiddleware(app, token=auth_token)
        logger.info("Bearer authentication enabled (MCP_AUTH_TOKEN is set).")
    else:
        logger.warning(
            "MCP_AUTH_TOKEN is not set — the HTTP endpoint is UNAUTHENTICATED. "
            "Anyone able to reach %s:%d can invoke every tool, including "
            "firewall policy changes. Set MCP_AUTH_TOKEN whenever this server "
            "is reachable beyond localhost.",
            host,
            port,
        )

    uvicorn.run(app, host=host, port=port, log_level=mcp.settings.log_level.lower())


def main() -> None:
    """Run the MCP server using stdio transport (default for Claude Desktop)."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        _run_http()
    else:
        logger.info("Starting FortiOS MCP Server on stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
