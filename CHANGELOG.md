# Changelog

All notable changes to this fork are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] — 2026-09-02

**Breaking:** `devices.yaml` is now the only way to configure a FortiGate. The
`FORTIOS_HOST` / `FORTIOS_API_TOKEN` environment pair and the inline
`FORTIOS_DEVICES` JSON are gone.

### Removed

Three ways to declare the same thing had accumulated: an inventory file, the
same structure inline as JSON, and a single-device environment fallback. Each
needed its own documentation, its own validation and its own tests, and the
fallback forced the inventory mount to stay commented out in
`docker-compose.yaml` — a single-device user had no `devices.yaml`, so Docker
would create a directory in its place. That in turn made a machine using an
inventory need a `docker-compose.override.yml` just to put the mount back.

One file removes all of it. `docker-compose.yaml` mounts `devices.yaml`
outright, and the override file is no longer needed for that.

### Changed

- The inventory path defaults to `./devices.yaml`, which resolves to
  `/app/devices.yaml` in the container. `FORTIOS_DEVICES_FILE` is now only for
  pointing somewhere else.
- A missing inventory says what to create and where, rather than naming an
  environment variable.
- Per-device `vdom`, `verify_ssl` and `timeout` were already inventory fields;
  the global `FORTIOS_VDOM` / `FORTIOS_VERIFY_SSL` / `FORTIOS_TIMEOUT` and
  `FORTIOS_DEVICE_NAME` variables no longer exist.

### Migration

A `.env` that looked like this:

```dotenv
FORTIOS_HOST=https://fw.example.com
FORTIOS_API_TOKEN=abc123
FORTIOS_VDOM=root
FORTIOS_VERIFY_SSL=true
```

becomes a `devices.yaml`:

```yaml
devices:
  - name: fw-hq-01
    host: https://fw.example.com
    api_token: ${FW_HQ_01_TOKEN}
    vdom: root
    verify_ssl: true
    default: true
```

with the token left in `.env` as `FW_HQ_01_TOKEN=abc123`. Tool calls are
unaffected: `fortigate` still defaults to the device marked `default: true`.

## [1.1.1] — 2026-09-02

A security release. Every dependency carrying a published advisory is updated,
and the workflows now declare least-privilege permissions. No behaviour or
configuration changes — upgrading is a pull and a restart.

### Security

`uv.lock` had drifted well behind what the declared constraints already allowed
— `mcp` sat at 1.26.0 while the `<2` cap permits 1.29.1 — leaving 28 open
Dependabot advisories against dependencies that were already fixable. All are
now closed:

| Package | From | To |
|---------|------|----|
| `click` | 8.3.1 | 8.5.0 |
| `cryptography` | 46.0.6 | 50.0.1 |
| `idna` | 3.11 | 3.19 |
| `mcp` | 1.26.0 | 1.29.1 |
| `pydantic-settings` | 2.13.1 | 2.15.0 |
| `pygments` | 2.19.2 | 2.21.0 |
| `pyjwt` | 2.12.1 | 2.13.0 |
| `pytest` | 9.0.2 | 9.1.1 |
| `python-multipart` | 0.0.22 | 0.0.32 |
| `starlette` | 1.0.0 | 1.6.0 |

The three that touch this server's request path:

- **`mcp`** — *HTTP transports serve session requests without verifying the
  authenticated principal* (high). The bearer middleware added in 1.1.0 already
  rejected a session id presented without a token, so a deployment with
  `MCP_AUTH_TOKEN` set was not exposed; one without it was.
- **`starlette`** — Host-header poisoning of `request.url.path`, bypassing
  path-based checks, plus a `request.form()` limit that was silently ignored.
- **`python-multipart`** — several parsing denial-of-service issues and
  parameter smuggling via `Content-Disposition` and querystring separators.

Only packages carrying advisories were upgraded, so the linters are unchanged
and CI's rule set did not move underneath this release.

`pip-audit` over the exported production and development requirements reports
no known vulnerabilities.

### Changed

- Every workflow job declares an explicit least-privilege `permissions` block.
  This was the entirety of what CodeQL flagged; it found nothing in the Python
  code.
- `container_name` comes from `FORTIOS_MCP_CONTAINER_NAME`, defaulting to the
  previous value, so a second copy — an `edge` or PR image — can run beside the
  live one instead of failing with a name conflict.
- `docker-compose.override.yml` is git-ignored, giving the inventory mount a
  home that survives a checkout without forcing it on single-device users.

### Added

- Images are published to GHCR and Docker Hub on every version tag, for
  `linux/amd64` and `linux/arm64`. Master pushes publish `edge`, and a pull
  request from a branch in the repository gets `pr-<number>`, removed when it
  closes. A weekly workflow keeps the ten most recent `sha-<commit>` tags.
- Dependabot version updates for Python dependencies, the Docker base image and
  the workflow actions, grouped into one pull request per ecosystem.

## [1.1.0] — 2026-08-31

First release of [this fork](https://github.com/ofaruk89/fortinet-mcp-server).
Everything below is additive: a single-device `.env` from 1.0.0 keeps working
unchanged, and no tool lost or renamed an existing parameter.

### Added

- **Multiple FortiGates from one server instance.** All 242 device tools take a
  `fortigate` parameter naming a device from an inventory, so a whole fleet is
  reachable from one deployment instead of one server per firewall — which would
  load 244 tool definitions into the client's context per firewall.
  - Inventory from `FORTIOS_DEVICES_FILE` (YAML or JSON), `FORTIOS_DEVICES`
    (inline JSON), or the existing `FORTIOS_HOST`/`FORTIOS_API_TOKEN` pair.
  - Per device: `vdom` (default `root`), `verify_ssl`, `timeout`, `site`,
    `tags`, `description`, `default`.
  - `${ENV_VAR}` references in `api_token` keep secrets out of the inventory
    file.
  - Default device resolves from `FORTIOS_DEFAULT_DEVICE`, then `default: true`,
    then the sole device.
  - Duplicate names, unset token references and an unknown default are rejected
    at startup with a message naming the offending device.
- **Fleet tools.** `fortios_devices_list` reports the inventory and the site and
  tag values in use, never tokens. `fortios_devices_check` probes reachability
  and firmware in parallel, narrowed by `devices`, `site` or `tags`, isolating a
  failure to the device it belongs to.
- **Group selection.** `site` and `tags` select which devices a fleet tool acts
  on; a device must carry every requested tag.
- **Container deployment.** Multi-stage `Dockerfile` (dependencies from
  `uv.lock`, non-root user) and `docker-compose.yaml` serving `streamable-http`.
  One `.env` variable drives both the container listen port and the published
  host port. Credentials are bind-mounted at run time and excluded from the
  image. Hardened: read-only rootfs, all capabilities dropped,
  no-new-privileges, resource limits.
- **HTTP transport authentication.** `MCP_AUTH_TOKEN` requires
  `Authorization: Bearer <token>` on every request, enforced ahead of MCP
  session handling so a leaked session id is useless on its own. Unset leaves
  the endpoint open and logs a warning at startup. stdio is unaffected.
- **DNS rebinding protection.** `MCP_ALLOWED_HOSTS` validates the `Host` header
  for clients connecting by IP or reverse-proxy hostname; loopback is always
  allowed and `Origin` values are derived from the host list.
- **Firewall rename tools.** `firewall_address_rename` and
  `firewall_addrgrp_rename` — `firewall_addrgrp` previously had no update path
  at all. Both send only the new name, so members, subnets and comments are
  untouched and FortiOS updates every reference.
- **Operational logging.** Unreachable devices and HTTP 401/403 log a warning
  naming the device, so a firewall that stops answering is visible in the server
  log instead of only in a tool response.
- **Tests.** 1,011, up from 1. The fleet suite walks the registered tool list and
  synthesises arguments from each tool's own schema, so a tool added later
  without the `fortigate` parameter fails the suite.

### Fixed

- **Transport failures reported as a blank error.** Connect timeouts, DNS
  failures and TLS errors surfaced as raw `httpx` exceptions whose `str()` is
  usually empty, producing `Error executing tool monitor_get:` with nothing after
  it. All ten request methods now wrap them into `FortiOSError`, which every tool
  already catches: `Cannot reach https://10.0.0.1: ConnectTimeout`.
- **A fresh install pulled an incompatible MCP SDK.** The `mcp[cli]>=1.9.0`
  requirement had no upper bound, so a plain `pip install` resolved mcp 2.x,
  where `mcp.server.fastmcp.FastMCP` — which this codebase uses — no longer
  exists, and the server died on its first import. Capped at `<2`. `uv sync`
  users were unaffected because `uv.lock` pinned 1.26.0.
- **The wheel shipped the whole repository.** `packages = ["."]` swept in
  `uv.lock` (over half the wheel), `.github/` and `tests/`. Now only the runtime
  is included: 56 KB instead of 154 KB.
- **`system_dhcp_server_create` rejected a call its own schema invited.**
  `lease_time` was declared `Field(default=86400)` with no Python default, so the
  schema advertised it as optional while the function required it positionally;
  omitting it raised `TypeError` instead of applying the documented 24-hour
  lease. Now defaulted, and moved after the required parameters.

### Changed

- Repository URLs, badges and issue links point at this fork. The original
  author remains in `authors`; the fork maintainer is named in `maintainers`,
  and `LICENSE` carries both copyright lines.
- README leads with what this fork adds, and the tool counts in Features are
  corrected — they claimed 204+ typed and 5 generic tools, against an actual
  233 typed, 9 generic pass-through and 2 fleet tools.

### Dependencies

- Added `pyyaml>=6.0`, imported lazily so a JSON inventory does not need it.
- `mcp[cli]` is now capped below 2.0 — see Fixed. Run `uv sync` after upgrading.

## [1.0.0]

Fork point — see the
[upstream project](https://github.com/paoloamato2/fortinet-mcp-server).

[Unreleased]: https://github.com/ofaruk89/fortinet-mcp-server/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/ofaruk89/fortinet-mcp-server/compare/v1.1.1...v2.0.0
[1.1.1]: https://github.com/ofaruk89/fortinet-mcp-server/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/ofaruk89/fortinet-mcp-server/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ofaruk89/fortinet-mcp-server/releases/tag/v1.0.0
