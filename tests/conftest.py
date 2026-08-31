"""Shared helpers for building the MCP Context tools expect."""

from types import SimpleNamespace
from typing import Any

from devices import DeviceConfig, DeviceRegistry
from fortios_client import FortiOSClient


async def _noop(*_: Any, **__: Any) -> None:
    """Stands in for ctx.error(), which tools call on a FortiOS error."""
    return None


def build_registry(**hosts: str) -> DeviceRegistry:
    """Build a registry from name -> host pairs; the first name is the default.

    The token is derived from the name so tests can assert that each device
    authenticates with its own credentials.
    """
    names = list(hosts)
    registry = DeviceRegistry(names[0])
    for name, host in hosts.items():
        config = DeviceConfig(name=name, host=host, api_token=f"token-{name}")
        registry.register(
            config,
            FortiOSClient(
                host=config.host, api_token=config.api_token, name=config.name
            ),
        )
    return registry


def build_ctx(registry: DeviceRegistry) -> Any:
    """Minimal stand-in for the MCP Context a tool receives."""
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={"client": registry.get(), "devices": registry}
        ),
        error=_noop,
    )
