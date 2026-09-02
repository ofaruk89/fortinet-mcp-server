"""Device inventory — configuration and client registry for multiple FortiGates.

A single MCP server instance talks to every FortiGate in the inventory, so the
tool surface stays flat (one set of tools, not one set per firewall) and a single
conversation can reach across sites.

Every FortiGate — one or fifty — is declared in ``devices.yaml``. There is no
second way to configure a device: one file, one format, whatever the size of
the estate.

``FORTIOS_DEVICES_FILE`` points somewhere other than ``./devices.yaml`` when
needed; the default resolves to ``/app/devices.yaml`` inside the container,
since that is the working directory.

Every device needs its own API token, since tokens are issued per FortiGate.
Reference them from the environment as ``${VAR}`` to keep the file free of
secrets, or write them inline — the file is git-ignored and never enters the
image either way.

There is no default device: every tool call names the FortiGate it targets, so
a question about "the firewall" is never answered from the wrong one.
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

    name: str = Field(
        description="Unique short name used by the fortigate tool parameter."
    )
    host: str = Field(description="Base URL, e.g. https://fw01.example.com:4443")
    api_token: str = Field(description="REST API token issued by this FortiGate.")
    vdom: str = "root"
    verify_ssl: bool = False
    timeout: float = 30.0

    # Grouping metadata. Not used to connect, but selectable: fleet tools take
    # a site or a tag list to narrow which devices they act on, and the values
    # are returned by fortios_devices_list so a model can choose for itself.
    site: str | None = Field(
        default=None,
        description="Location or grouping, e.g. hq. Selectable as a whole.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Free-form labels, e.g. [edge, ha-primary]. Selectable.",
    )
    description: str | None = None

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
            f"No device inventory at {path}. Copy devices.example.yaml to "
            "devices.yaml and fill in your FortiGates, or set "
            "FORTIOS_DEVICES_FILE to another path. In Docker, check the file is "
            "mounted into the container — a missing bind-mount source makes "
            "Docker create a directory there instead."
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


DEFAULT_INVENTORY = "devices.yaml"


def load_devices() -> list[DeviceConfig]:
    """Read the device inventory. One file, whether it holds one device or fifty."""
    devices = _load_inventory_file(
        os.environ.get("FORTIOS_DEVICES_FILE", "").strip() or DEFAULT_INVENTORY
    )

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


class DeviceRegistry:
    """Holds one live FortiOSClient per configured device."""

    def __init__(self) -> None:
        self._clients: dict[str, FortiOSClient] = {}
        self._configs: dict[str, DeviceConfig] = {}

    def register(self, config: DeviceConfig, client: FortiOSClient) -> None:
        self._configs[config.name] = config
        self._clients[config.name] = client

    def get(self, device: str) -> FortiOSClient:
        """Return the client for `device`. Every call names one FortiGate."""
        client = self._clients.get(device.strip())
        if client is None:
            raise UnknownDeviceError(
                f"Unknown device {device!r}. Configured devices: "
                f"{', '.join(self.names())}. "
                "Call fortios_devices_list to see the inventory."
            )
        return client

    def names(self) -> list[str]:
        return sorted(self._clients)

    def select(
        self,
        devices: list[str] | None = None,
        site: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Resolve a selection to device names.

        An explicit `devices` list wins. Otherwise `site` and `tags` both
        narrow the full inventory, and a device must carry *every* requested
        tag to match, so [edge, ha-primary] picks the primary edge firewalls
        rather than everything edge-ish. With no arguments, the whole fleet.
        """
        if devices:
            unknown = [d for d in devices if d not in self._clients]
            if unknown:
                raise UnknownDeviceError(
                    f"Unknown device(s): {', '.join(unknown)}. "
                    f"Configured devices: {', '.join(self.names())}."
                )
            return list(devices)

        matched = list(self._configs.values())
        if site:
            matched = [c for c in matched if c.site == site]
            if not matched:
                known = sorted({c.site for c in self._configs.values() if c.site})
                raise UnknownDeviceError(
                    f"No device has site {site!r}. Known sites: "
                    f"{', '.join(known) if known else 'none configured'}."
                )
        if tags:
            wanted = set(tags)
            matched = [c for c in matched if wanted.issubset(set(c.tags))]
            if not matched:
                known = sorted({t for c in self._configs.values() for t in c.tags})
                scope = f" at site {site!r}" if site else ""
                raise UnknownDeviceError(
                    f"No device{scope} carries every tag in {sorted(wanted)}. "
                    f"Known tags: {', '.join(known) if known else 'none configured'}."
                )
        return sorted(c.name for c in matched)

    def groups(self) -> dict[str, list[str]]:
        """Selectable site and tag values, for the inventory tool to advertise."""
        return {
            "sites": sorted({c.site for c in self._configs.values() if c.site}),
            "tags": sorted({t for c in self._configs.values() for t in c.tags}),
        }

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
            }
            for c in sorted(self._configs.values(), key=lambda c: c.name)
        ]


def client_for(ctx: Any, device: str) -> FortiOSClient:
    """Resolve the FortiOSClient a tool call targets.

    Single entry point for every tool: each tool resolves its client here from
    its `fortigate` parameter, so routing lives in one place.
    """
    registry: DeviceRegistry = ctx.request_context.lifespan_context["devices"]
    return registry.get(device)
