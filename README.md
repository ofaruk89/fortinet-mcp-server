# FortiOS 7.6.x MCP Server

<p align="center">
  <img src="https://img.shields.io/badge/FortiOS-7.6.x-EE3124?style=for-the-badge&logo=fortinet&logoColor=white" alt="FortiOS version">
  <img src="https://img.shields.io/badge/MCP-Model_Context_Protocol-5A67D8?style=for-the-badge" alt="MCP">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/github/license/ofaruk89/fortinet-mcp-server?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/github/stars/ofaruk89/fortinet-mcp-server?style=for-the-badge" alt="Stars">
</p>

<p align="center">
  <strong>A complete <a href="https://modelcontextprotocol.io">Model Context Protocol (MCP)</a> server for Fortinet FortiOS 7.6.x — exposing the entire REST API (1536 endpoints) as typed MCP tools usable from Claude Desktop, Cursor, or any MCP-compatible client.</strong>
</p>

<p align="center">
  <sub>A fork of <a href="https://github.com/paoloamato2/fortinet-mcp-server">paoloamato2/fortinet-mcp-server</a>, extended with a container deployment, bearer-authenticated HTTP transport, and additional firewall tools.</sub>
</p>

---

## Table of Contents

- [Features](#features)
- [Tool Categories](#tool-categories)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
  - [1. Create API Token on FortiGate](#1-create-api-token-on-fortigate)
  - [2. Install dependencies](#2-install-dependencies)
  - [3. Configure environment](#3-configure-environment)
  - [4. Run with MCP Inspector](#4-run-with-mcp-inspector)
  - [5. Install in Claude Desktop](#5-install-in-claude-desktop)
- [Multiple Devices](#multiple-devices)
- [HTTP Mode](#http-mode)
- [Docker](#docker)
- [Usage Examples](#usage-examples)
- [Project Structure](#project-structure)
- [Security Notes](#security-notes)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **204+ typed MCP tools** organized by functional area (system, firewall, VPN, router, user, monitor, log, security, wireless)
- **5 generic pass-through tools** that cover all 1,536 FortiOS API endpoints
- Async HTTP client with Bearer-token authentication via `httpx`
- Full support for **CMDB, Monitor, Log, and Service** API sections
- Configurable SSL verification (self-signed certificates supported)
- Compatible with **multi-VDOM** environments
- **Multiple FortiGates from one server** — every tool takes a `fortigate` parameter, so one instance serves a whole fleet
- Runs as **stdio** (Claude Desktop) or **HTTP** server (remote/cloud use)

---

## Tool Categories

| Module | # Tools | Description |
|--------|--------:|-------------|
| **Generic** | 5 | `cmdb_list/get/create/update/delete`, `monitor_get/action`, `log_get`, `service_call` — cover **ALL** endpoints |
| **System** | 27 | Interfaces, DNS, NTP, admins, DHCP, SNMP, certificates, VDOMs, syslog |
| **Firewall** | 34 | Policies (IPv4/IPv6), addresses, address groups (create/rename/delete), services, VIPs, IP pools, schedules, sessions |
| **VPN** | 22 | IPsec Phase 1/2, SSL VPN portals/settings, tunnel up/down, VPN certificates |
| **Router** | 17 | Static routes, OSPF, BGP, RIP, prefix lists, route maps, SD-WAN health |
| **User** | 18 | Local users, groups, RADIUS, LDAP, TACACS+, SAML, authenticated sessions |
| **Monitor** | 18 | ARP, FortiView top talkers, endpoint control, IPS stats, switch controller, config backup |
| **Log** | 18 | Traffic, event, VPN, user, virus, webfilter, IPS, app-ctrl, DNS logs + log config |
| **Security** | 29 | IPS, AV, webfilter, app control, DLP, email filter, DNS filter, WAF, ICAP, ssh-filter, ZTNA |
| **Wireless** | 18 | AP profiles, WTPs, SSIDs (VAPs), Hotspot 2.0, connected clients, rogue APs |

**Total: 204+ tools**

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.11+ |
| Package manager | `uv` (recommended) or `pip` |
| FortiGate | FortiOS 7.6.x |
| Auth | REST API admin account with Bearer token |

---

## Quick Start

### 1. Create API Token on FortiGate

1. Log into your FortiGate Web UI
2. Navigate to **System > Administrators**
3. Click **Create New > REST API Admin**
4. Assign an admin profile (`super_admin` for full access, or a restricted profile following least-privilege)
5. Copy the generated **API token** — it is shown only once

### 2. Install dependencies

```bash
git clone https://github.com/ofaruk89/fortinet-mcp-server.git
cd fortinet-mcp-server

# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
FORTIOS_HOST=https://192.168.1.1
FORTIOS_API_TOKEN=your-token-here
FORTIOS_VDOM=root
FORTIOS_VERIFY_SSL=false
FORTIOS_TIMEOUT=30
```

### 4. Run with MCP Inspector

```bash
uv run mcp dev server.py
```

### 5. Install in Claude Desktop

```bash
uv run mcp install server.py --name "FortiOS"
```

Or manually add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fortios": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/fortinet-mcp-server",
        "python", "server.py"
      ],
      "env": {
        "FORTIOS_HOST": "https://192.168.1.1",
        "FORTIOS_API_TOKEN": "your-api-token",
        "FORTIOS_VDOM": "root",
        "FORTIOS_VERIFY_SSL": "false"
      }
    }
  }
}
```

> On **macOS**, `claude_desktop_config.json` is at `~/Library/Application Support/Claude/claude_desktop_config.json`.  
> On **Windows**, it is at `%APPDATA%\Claude\claude_desktop_config.json`.

---

## Multiple Devices

One server instance can serve many FortiGates. This keeps the tool surface flat
— running one instance per firewall would load 240+ tool definitions into the
client's context *per firewall* — and lets a single conversation reach across
sites.

Every tool takes an optional `fortigate` parameter naming a firewall from the
inventory. Omitting it targets the default device.

```python
firewall_policy_list(fortigate="aef01", filter_action="deny")
monitor_vpn_ipsec(fortigate="bef01")
```

The parameter is named `fortigate` rather than `device` because FortiOS already
uses `device` for the egress interface of a static route
(`router_static_create(device="wan1")`).

### Inventory

Define the fleet in a YAML (or JSON) file and point `FORTIOS_DEVICES_FILE` at
it. Start from [`devices.example.yaml`](devices.example.yaml):

```yaml
devices:
  - name: aef01
    host: https://aef01.example.local:4443
    api_token: ${AEF01_TOKEN}     # ${VAR} keeps the secret out of the file
    site: aef
    tags: [edge, primary]
    verify_ssl: true
    default: true                 # targeted when `device` is omitted
  - name: bef01
    host: https://bef01.example.local:4443
    api_token: ${BEF01_TOKEN}
    site: bef
```

Keep the tokens in `.env` and reference them from the inventory, so the
inventory file itself holds no secrets:

```dotenv
FORTIOS_DEVICES_FILE=./devices.yaml
AEF01_TOKEN=...
BEF01_TOKEN=...
```

Each FortiGate issues its own API token, so every device needs its own. Only
`name`, `host` and `api_token` are required; `vdom`, `verify_ssl` and `timeout`
default to the same values as the single-device setup. `site`, `tags` and
`description` are metadata returned by the inventory tools so a model can pick
devices by site or role.

For a handful of devices the same structure can live inline in `.env` instead,
as JSON:

```dotenv
FORTIOS_DEVICES=[{"name":"aef01","host":"https://aef01.local:4443","api_token":"...","default":true}]
```

If neither variable is set, the original `FORTIOS_HOST` / `FORTIOS_API_TOKEN`
pair is registered as a single device — **existing single-device deployments
need no changes**. Name it with `FORTIOS_DEVICE_NAME` (default: `default`).

`devices.yaml` holds one API token per firewall: it is git-ignored and excluded
from the Docker image, exactly like `.env`.

### Choosing the default

The device a call targets when `fortigate` is omitted, in order of precedence:
`FORTIOS_DEFAULT_DEVICE` → the device marked `default: true` → the only device.
With several devices and no default marked, the first is used and a warning is
logged at startup.

### Inventory tools

| Tool | Purpose |
|------|---------|
| `fortios_devices_list` | Names, sites, tags and which device is the default. Never returns tokens. |
| `fortios_devices_check` | Probes reachability and firmware of several devices in parallel (read-only). Accepts `devices` or `site`; a failure on one device is reported for that device only. |

### Coverage

All 242 tools take `fortigate` — the nine generic pass-through tools and every
typed tool across firewall, system, VPN, router, user, monitor, log, security
and wireless. The only tools without it are the two fleet tools below, which
address the inventory rather than a single device.

### Docker

Mount the inventory next to `.env` and point the variable at the container path:

```yaml
volumes:
  - ./.env:/app/.env:ro
  - ./devices.yaml:/app/devices.yaml:ro
```

```dotenv
FORTIOS_DEVICES_FILE=/app/devices.yaml
AEF01_TOKEN=...
```

The volume line is present but commented out in `docker-compose.yaml`. Create
`devices.yaml` before uncommenting it — Docker creates a directory in place of a
missing bind-mount source.

---

## HTTP Mode

To run as a remote HTTP server instead of stdio:

```bash
MCP_TRANSPORT=streamable-http MCP_PORT=8000 uv run server.py
```

Connect via `http://localhost:8000/mcp`.

This mode is useful for shared team setups or cloud-hosted deployments. Two
environment variables secure it — both are optional and apply to HTTP mode only,
never to stdio:

| Variable | Purpose |
|----------|---------|
| `MCP_AUTH_TOKEN` | Requires `Authorization: Bearer <token>` on every request. Empty means unauthenticated (a startup warning is logged). |
| `MCP_ALLOWED_HOSTS` | Extra `Host` header values accepted, for clients connecting via an IP or reverse-proxy hostname. Loopback is always allowed. |

```bash
MCP_TRANSPORT=streamable-http MCP_PORT=8000 \
MCP_AUTH_TOKEN=$(openssl rand -hex 32) \
MCP_ALLOWED_HOSTS='["10.0.0.5:8000"]' \
  uv run server.py
```

See [Authentication](#authentication) and
[Connecting from another host](#connecting-from-another-host) for details.

### Connecting Claude Desktop to an HTTP server

Claude Desktop cannot send a custom `Authorization` header to a remote MCP
server directly, so bridge it with `mcp-remote` (requires Node.js):

```json
{
  "mcpServers": {
    "fortios": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://10.0.0.5:8000/mcp",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer your-mcp-auth-token"
      }
    }
  }
}
```

The token goes in `env` rather than inline in `args` because Claude Desktop
mangles spaces inside a single argument.

If `mcp-remote` refuses a plain-HTTP URL to a non-localhost host, tunnel it and
point the config at `http://127.0.0.1:8000/mcp` — loopback needs no
`MCP_ALLOWED_HOSTS` entry:

```bash
ssh -N -L 8000:127.0.0.1:8000 user@10.0.0.5
```

---

## Docker

The repository ships a `Dockerfile` and `docker-compose.yaml` that run the server
in **streamable-http** mode. All configuration comes from a single `.env` file:
Compose reads it for variable substitution, and the same file is bind-mounted
read-only to `/app/.env` inside the container, where `load_dotenv()` picks it up.

```bash
cp .env.example .env
chmod 644 .env          # must be readable by the container user
# edit .env: FORTIOS_HOST, FORTIOS_API_TOKEN, and MCP_PORT if 8000 is taken

docker compose up -d --build
```

The server is then reachable at `http://127.0.0.1:8000/mcp` (or whatever
`MCP_PORT` you set).

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_PORT` | `8000` | Port the container listens on **and** the host port published — change it in `.env` only |
| `MCP_BIND_ADDRESS` | `127.0.0.1` | Host **IP** the port is published on — no port here, `MCP_PORT` supplies it |
| `MCP_TRANSPORT` | `streamable-http` | Transport used inside the container |
| `MCP_AUTH_TOKEN` | *(empty)* | Bearer token required on every HTTP request; empty disables authentication |
| `MCP_ALLOWED_HOSTS` | *(empty)* | Extra `Host` header values accepted (DNS rebinding protection) |

### Authentication

Set `MCP_AUTH_TOKEN` in `.env` to require a bearer token on every HTTP request:

```bash
openssl rand -hex 32          # generate a token
```

```dotenv
MCP_AUTH_TOKEN=<your-token>
```

Clients then send `Authorization: Bearer <token>`; anything else gets
`401 Unauthorized` with `WWW-Authenticate: Bearer`. The check runs ahead of MCP
session handling, so a leaked session id is useless without the token. When
`MCP_AUTH_TOKEN` is empty the server logs a warning at startup and serves
unauthenticated — acceptable only on a loopback bind.

Authentication applies to HTTP mode only; stdio (Claude Desktop) is unaffected.

### Connecting from another host

`Host` header validation (DNS rebinding protection) is enabled as soon as
`MCP_ALLOWED_HOSTS` is set. List the value that appears in the **client's
connection URL** — not the client's IP — as a JSON array or comma-separated
list. Loopback is always allowed on top of the list.

```dotenv
MCP_BIND_ADDRESS=10.211.112.12
MCP_ALLOWED_HOSTS=["10.211.112.12:8002"]
```

The port must match the port clients connect to, or requests are rejected with
`421 Invalid Host header`. Use `["10.211.112.12:*"]` to accept any port, or a
hostname (`["mcp.example.com"]`) when a reverse proxy sits in front. Leaving the
variable empty disables the check, which is fine behind a loopback bind.

To move the server to another port, edit one line and re-apply:

```bash
sed -i 's/^MCP_PORT=.*/MCP_PORT=8002/' .env
docker compose up -d
```

### Operating

```bash
docker compose ps                 # health status and port mapping
docker compose logs -f fortios-mcp
docker compose down
```

> **Create `.env` before the first `up`.** If the file does not exist, Docker
> creates a *directory* in its place and `load_dotenv()` finds nothing.

> **Troubleshooting:** in HTTP mode the FortiGate client is created per MCP
> session, so a bad or incomplete `.env` does **not** stop the container — it
> starts, reports `healthy` (the healthcheck only probes the TCP port), and each
> client session fails instead. If a client cannot connect, check
> `docker compose logs fortios-mcp` for
> `Required environment variable 'FORTIOS_HOST' is not set`. A successful
> session logs `Connecting to FortiGate <host> (vdom=..., ssl-verify=...)`.

> **Security:** the MCP endpoint exposes all 204+ tools, including firewall
> policy create/delete. Never bind it beyond `127.0.0.1` without setting
> `MCP_AUTH_TOKEN`. The `.env` file is never copied into the image (it is listed
> in `.dockerignore`) — keep it out of version control too.

---

## Usage Examples

### Via Claude Desktop

Once installed, you can ask Claude natural-language questions such as:

- *"Show me all firewall policies that deny traffic"*
- *"Which IPsec tunnels are currently down?"*
- *"List all interfaces with their IP addresses"*
- *"Which route would be used to reach 8.8.8.8?"*
- *"Show the top 20 traffic sources in FortiView"*
- *"Are there any failed admin login attempts in the logs?"*

### Direct Tool Invocations

```python
# List firewall policies filtered by action
firewall_policy_list(filter_action="deny")

# Get system status
system_status()

# Rename an address object or address group (references are updated by FortiOS)
firewall_address_rename(name="old-server", new_name="web-server-01")
firewall_addrgrp_rename(name="old-group", new_name="dmz-servers")

# Check IPsec VPN tunnels
monitor_vpn_ipsec()

# Query forward traffic logs for a specific source IP
log_traffic_forward(srcip="10.10.1.100", rows=50)

# Generic: list any CMDB resource (full API coverage)
cmdb_list("casb/profile")
cmdb_list("wireless-controller.hotspot20/hs-profile")

# Generic: get any monitor data
monitor_get("registration/forticloud")
```

---

## Project Structure

```
fortinet-mcp-server/
├── server.py              # FastMCP entry point, lifespan, tool registration
├── fortios_client.py      # Async HTTP client (CMDB/Monitor/Log/Service)
├── pyproject.toml         # Project metadata and dependencies
├── .env.example           # Environment variable template
├── Dockerfile             # Multi-stage image (uv + python:3.12-slim)
├── docker-compose.yaml    # HTTP-mode service, .env mounted to /app/.env
├── README.md              # This file
└── tools/
    ├── __init__.py
    ├── generic.py         # Generic pass-through tools (all 1536 endpoints)
    ├── system.py          # System config + monitoring
    ├── firewall.py        # Firewall policies, addresses, VIPs, sessions
    ├── vpn.py             # IPsec + SSL VPN config and monitoring
    ├── router.py          # Static routes, OSPF, BGP, SD-WAN
    ├── user.py            # Local users, groups, RADIUS, LDAP, sessions
    ├── monitor.py         # Network monitoring, FortiView, endpoint control
    ├── log.py             # Log retrieval and configuration
    ├── security.py        # IPS, AV, webfilter, DLP, WAF, ZTNA profiles
    └── wireless.py        # WiFi APs, SSIDs, clients, rogue APs
```

---

## Security Notes

- The API token grants the same access level as its associated admin profile. Follow the **principle of least privilege** — create a restricted profile if you only need read access.
- Set `FORTIOS_VERIFY_SSL=true` in production and ensure your FortiGate has a valid TLS certificate.
- The server runs **locally over stdio** by default — it is not exposed over the network unless HTTP mode is enabled.
- In HTTP mode, **always set `MCP_AUTH_TOKEN`** before binding beyond `127.0.0.1`. Without it every tool — including firewall policy create/delete — is reachable unauthenticated by anyone who can open the port. Set `MCP_ALLOWED_HOSTS` as well when clients connect via an IP or hostname, and rotate the token as you would the FortiOS one.
- **Never commit your `.env` file or expose your API token** in logs, issues, or code.
- Rotate your API token regularly and revoke it immediately if compromised.

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

- Bug reports and feature requests → [open an issue](https://github.com/ofaruk89/fortinet-mcp-server/issues)
- Security vulnerabilities → see [SECURITY.md](SECURITY.md)
- Code of conduct → [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

---

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

> **Disclaimer:** This project is not affiliated with or endorsed by Fortinet, Inc. FortiOS and FortiGate are trademarks of Fortinet, Inc.
