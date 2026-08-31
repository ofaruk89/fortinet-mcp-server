"""Device inventory — configuration and client registry for multiple FortiGates.

A single MCP server instance talks to every FortiGate in the inventory, so the
tool surface stays flat (one set of tools, not one set per firewall) and a single
conversation can reach across sites.

The inventory is read from the first source that is present:

1. ``FORTIOS_DEVICES_FILE`` — path to a YAML or JSON file (recommended for more
   than a handful of devices; supports comments and per-device metadata).
2. ``FORTIOS_DEVICES`` — the same structure inline as JSON, for small setups
   that would rather keep everything in ``.env``.
3. ``FORTIOS_HOST`` / ``FORTIOS_API_TOKEN`` / … — the original single-device
   variables, registered as one device. Existing deployments keep working
   unchanged.

Every device needs its own API token, since tokens are issued per FortiGate.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from fortios_client import FortiOSClient

logger = logging.getLogger("fortios_mcp")


class DeviceConfigError(Exception):
    """The device inventory is missing, malformed or inconsistent."""


class UnknownDeviceError(Exception):
    """A tool asked for a device name that is not in the inventory."""


class DeviceConfig(BaseModel):
    """Connection settings for one FortiGate."""

    name: str = Field(description="Unique short name used by the device parameter.")
    host: str = Field(description="Base URL, e.g. https://fw01.example.com:4443")
    api_token: str = Field(description="REST API token issued by this FortiGate.")
    vdom: str = "root"
    verify_ssl: bool = False
    timeout: float = 30.0

    # Grouping metadata — not used to connect, but returned by the inventory
    # tools so a model can pick devices by site or role.
    site: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None

    # Marks the device used when a tool call omits `device`.
    default: bool = False

    @field_validator("host")
    @classmethod
    def _require_scheme(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                f"host must start with https:// or http:// (got {value!r})"
            )
        return value

    @field_validator("api_token")
    @classmethod
    def _expand_token(cls, value: str) -> str:
        # Allows an inventory file to reference a token by environment variable
        # (api_token: ${FW01_TOKEN}) instead of storing the secret in the file.
        expanded = os.path.expandvars(value.strip())
        if not expanded or expanded.startswith("$"):
            raise ValueError(
                "api_token is empty or references an unset environment variable "
                f"({value!r})"
            )
        return expanded


def _devices_from_payload(payload: Any, source: str) -> list[DeviceConfig]:
    """Accept either {'devices': [...]} or a bare list of devices."""
    if isinstance(payload, dict):
        payload = payload.get("devices", payload.get("Devices"))
    if not isinstance(payload, list) or not payload:
        raise DeviceConfigError(
            f"{source} must contain a non-empty 'devices' list (or be a bare list)."
        )

    devices: list[DeviceConfig] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise DeviceConfigError(f"{source}: device #{index + 1} is not a mapping.")
        try:
            devices.append(DeviceConfig(**entry))
        except Exception as exc:
            label = entry.get("name") or f"#{index + 1}"
            raise DeviceConfigError(
                f"{source}: device {label} is invalid — {exc}"
            ) from None
    return devices


def _load_inventory_file(path_str: str) -> list[DeviceConfig]:
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise DeviceConfigError(
            f"FORTIOS_DEVICES_FILE points at {path}, which is not a readable file. "
            "When running in Docker, check that the file is mounted into the container."
        )
    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml  # imported lazily so JSON inventories need no YAML support

        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise DeviceConfigError(f"{path} is not valid YAML — {exc}") from None
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DeviceConfigError(f"{path} is not valid JSON — {exc}") from None

    return _devices_from_payload(payload, str(path))


def _load_single_device_env() -> list[DeviceConfig]:
    """Fall back to the original FORTIOS_* variables as a one-device inventory."""
    host = os.environ.get("FORTIOS_HOST", "").strip()
    token = os.environ.get("FORTIOS_API_TOKEN", "").strip()
    if not host or not token:
        raise DeviceConfigError(
            "No device inventory found. Set FORTIOS_DEVICES_FILE, FORTIOS_DEVICES, "
            "or the single-device pair FORTIOS_HOST + FORTIOS_API_TOKEN. "
            "Copy .env.example to .env and fill in your values."
        )
    return [
        DeviceConfig(
            name=os.environ.get("FORTIOS_DEVICE_NAME", "default").strip() or "default",
            host=host,
            api_token=token,
            vdom=os.environ.get("FORTIOS_VDOM", "root").strip() or "root",
            verify_ssl=os.environ.get("FORTIOS_VERIFY_SSL", "false").lower()
            in ("1", "true", "yes"),
            timeout=float(os.environ.get("FORTIOS_TIMEOUT", "30")),
            default=True,
        )
    ]


def load_devices() -> list[DeviceConfig]:
    """Build the device inventory from the environment."""
    inventory_file = os.environ.get("FORTIOS_DEVICES_FILE", "").strip()
    inline = os.environ.get("FORTIOS_DEVICES", "").strip()

    if inventory_file:
        devices = _load_inventory_file(inventory_file)
    elif inline:
        try:
            payload = json.loads(inline)
        except json.JSONDecodeError as exc:
            raise DeviceConfigError(
                f"FORTIOS_DEVICES is not valid JSON — {exc}"
            ) from None
        devices = _devices_from_payload(payload, "FORTIOS_DEVICES")
    else:
        devices = _load_single_device_env()

    seen: dict[str, int] = {}
    for device in devices:
        seen[device.name] = seen.get(device.name, 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        raise DeviceConfigError(
            f"Duplicate device names in the inventory: {', '.join(duplicates)}. "
            "Device names are how tools address a firewall, so they must be unique."
        )
    return devices


def resolve_default(devices: list[DeviceConfig]) -> str:
    """Pick the device a tool call targets when it omits `device`."""
    override = os.environ.get("FORTIOS_DEFAULT_DEVICE", "").strip()
    names = [d.name for d in devices]

    if override:
        if override not in names:
            raise DeviceConfigError(
                f"FORTIOS_DEFAULT_DEVICE={override!r} is not in the inventory "
                f"({', '.join(names)})."
            )
        return override

    marked = [d.name for d in devices if d.default]
    if len(marked) > 1:
        raise DeviceConfigError(
            f"More than one device is marked default: {', '.join(marked)}. "
            "Mark exactly one, or set FORTIOS_DEFAULT_DEVICE."
        )
    if marked:
        return marked[0]

    if len(devices) > 1:
        logger.warning(
            "No default device configured — falling back to %r. Mark one device "
            "with 'default: true' or set FORTIOS_DEFAULT_DEVICE to make this explicit.",
            names[0],
        )
    return names[0]


class DeviceRegistry:
    """Holds one live FortiOSClient per configured device."""

    def __init__(self, default_name: str) -> None:
        self._clients: dict[str, FortiOSClient] = {}
        self._configs: dict[str, DeviceConfig] = {}
        self._default = default_name

    @property
    def default_name(self) -> str:
        return self._default

    def register(self, config: DeviceConfig, client: FortiOSClient) -> None:
        self._configs[config.name] = config
        self._clients[config.name] = client

    def get(self, device: str | None = None) -> FortiOSClient:
        """Return the client for `device`, or the default device when omitted."""
        name = (device or self._default).strip()
        client = self._clients.get(name)
        if client is None:
            raise UnknownDeviceError(
                f"Unknown device {name!r}. Configured devices: "
                f"{', '.join(sorted(self._clients))}. "
                "Call fortios_devices_list to see the inventory."
            )
        return client

    def names(self) -> list[str]:
        return sorted(self._clients)

    def select(
        self, devices: list[str] | None = None, site: str | None = None
    ) -> list[str]:
        """Resolve a device/site selection to device names."""
        if devices:
            unknown = [d for d in devices if d not in self._clients]
            if unknown:
                raise UnknownDeviceError(
                    f"Unknown device(s): {', '.join(unknown)}. "
                    f"Configured devices: {', '.join(self.names())}."
                )
            return list(devices)
        if site:
            matched = [n for n, c in self._configs.items() if c.site == site]
            if not matched:
                sites = sorted({c.site for c in self._configs.values() if c.site})
                raise UnknownDeviceError(
                    f"No device has site {site!r}. Known sites: "
                    f"{', '.join(sites) if sites else 'none configured'}."
                )
            return sorted(matched)
        return self.names()

    def describe(self) -> list[dict[str, Any]]:
        """Inventory without secrets — safe to return from a tool."""
        return [
            {
                "name": c.name,
                "host": c.host,
                "vdom": c.vdom,
                "verify_ssl": c.verify_ssl,
                "site": c.site,
                "tags": c.tags,
                "description": c.description,
                "is_default": c.name == self._default,
            }
            for c in sorted(self._configs.values(), key=lambda c: c.name)
        ]


def client_for(ctx: Any, device: str | None = None) -> FortiOSClient:
    """Resolve the FortiOSClient a tool call targets.

    Single entry point for every tool, so adding the `device` parameter to a
    tool is a one-line change at its client lookup.
    """
    registry: DeviceRegistry = ctx.request_context.lifespan_context["devices"]
    return registry.get(device)
