"""Fleet-wide coverage: every tool must honour the `fortigate` parameter.

Rather than hand-writing a case per tool, these tests walk the registered tool
list, synthesise arguments from each tool's own JSON schema and assert that the
resulting request went to the named device with that device's credentials. A
tool added later without the parameter fails here.
"""

import json
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from conftest import build_ctx, build_registry
from server import mcp

# Fleet tools address the inventory rather than acting on a device through it,
# so they take `devices` / `site` / `tags` instead of a single `fortigate`.
FLEET_TOOLS = {"fortios_devices_list", "fortios_devices_check"}

ALL_TOOLS = sorted(t.name for t in mcp._tool_manager.list_tools())
DEVICE_TOOLS = [name for name in ALL_TOOLS if name not in FLEET_TOOLS]


def _schema(name: str) -> dict[str, Any]:
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None
    return tool.parameters


def _synth(name: str, spec: dict[str, Any]) -> Any:
    """Invent a plausible value for one required parameter."""
    kind = spec.get("type")
    if kind is None and "anyOf" in spec:
        kind = next(
            (o.get("type") for o in spec["anyOf"] if o.get("type") != "null"), "string"
        )
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        return ["test"]
    if kind == "object":
        return {}
    # Several tools take a JSON document as a string and parse it.
    blob = "json" in name.lower() or name in ("data", "body", "payload")
    if blob or "JSON" in spec.get("description", ""):
        return "{}"
    return "test"


def _args(tool_name: str) -> dict[str, Any]:
    schema = _schema(tool_name)
    required = schema.get("required", [])
    return {
        name: _synth(name, spec)
        for name, spec in schema.get("properties", {}).items()
        if name in required and name not in ("fortigate", "vdom")
    }


# ── Completeness ──────────────────────────────────────────────────────


def test_the_tool_list_is_the_expected_size() -> None:
    # 9 generic pass-through + 233 typed + 2 fleet tools.
    assert len(ALL_TOOLS) == 244
    assert len(DEVICE_TOOLS) == 242


@pytest.mark.parametrize("tool_name", DEVICE_TOOLS)
def test_every_tool_exposes_the_fortigate_parameter(tool_name: str) -> None:
    properties = _schema(tool_name).get("properties", {})

    assert "fortigate" in properties, f"{tool_name} cannot target a device"
    # Required, not optional: there is no default device, so a call that does
    # not name one must fail at the schema rather than pick a firewall itself.
    assert "fortigate" in _schema(tool_name).get("required", []), (
        f"{tool_name} would let a call omit the device"
    )


def test_no_tool_reads_the_shared_client_directly() -> None:
    """Every tool must resolve its client through the registry."""
    import pathlib

    offenders = [
        path.name
        for path in pathlib.Path("tools").glob("*.py")
        if 'lifespan_context["client"]' in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_router_static_keeps_its_own_device_field() -> None:
    """FortiOS calls the egress interface `device`; that must not be shadowed."""
    properties = _schema("router_static_create")["properties"]

    assert "Egress interface" in properties["device"]["description"]
    assert "inventory" in properties["fortigate"]["description"]


# ── Routing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", DEVICE_TOOLS)
@pytest.mark.asyncio
async def test_every_tool_routes_to_the_named_device(
    httpx_mock: HTTPXMock, tool_name: str
) -> None:
    httpx_mock.add_response(
        json={"status": "success", "results": []}, is_reusable=True, is_optional=True
    )
    registry = build_registry(aef01="https://aef01.test", bef01="https://bef01.test")

    async with registry.get("aef01"), registry.get("bef01"):
        result = await _tool_call(tool_name, registry, fortigate="bef01")

    # A tool may reject synthesised arguments before calling out (an update tool
    # with nothing to update, say). What must never happen is a request landing
    # on a device other than the one named.
    requests = httpx_mock.get_requests()
    if not requests:
        assert "error" in result, f"{tool_name} neither called out nor reported why"
        return

    for request in requests:
        assert request.url.host == "bef01.test", f"{tool_name} hit the wrong device"
        assert request.headers["Authorization"] == "Bearer token-bef01"


@pytest.mark.parametrize("tool_name", DEVICE_TOOLS)
@pytest.mark.asyncio
async def test_every_tool_rejects_an_unknown_device_without_calling_out(
    httpx_mock: HTTPXMock, tool_name: str
) -> None:
    registry = build_registry(aef01="https://aef01.test")

    async with registry.get("aef01"):
        result = await _tool_call(tool_name, registry, fortigate="not-in-inventory")

    assert isinstance(result, dict)
    assert "Unknown device" in json.dumps(result), tool_name
    assert httpx_mock.get_requests() == [], f"{tool_name} called out anyway"


async def _tool_call(tool_name: str, registry: Any, **overrides: Any) -> dict[str, Any]:
    tool = mcp._tool_manager.get_tool(tool_name)
    assert tool is not None
    return await tool.fn(ctx=build_ctx(registry), **_args(tool_name), **overrides)


# ── Read-only gate ────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", DEVICE_TOOLS)
@pytest.mark.asyncio
async def test_no_tool_can_change_a_firewall_while_writes_are_off(
    httpx_mock: HTTPXMock, tool_name: str
) -> None:
    """Read-only means read-only: nothing but GET may leave the process."""
    httpx_mock.add_response(
        json={"status": "success", "results": []}, is_reusable=True, is_optional=True
    )
    registry = build_registry(allow_writes=False, aef01="https://aef01.test")

    async with registry.get("aef01"):
        await _tool_call(tool_name, registry, fortigate="aef01")

    for request in httpx_mock.get_requests():
        assert request.method == "GET", (
            f"{tool_name} issued {request.method} with writes disabled"
        )
