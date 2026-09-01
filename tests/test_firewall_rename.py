"""Tests for the firewall address / address group rename tools."""

import json
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from conftest import build_ctx, build_registry
from server import mcp


def _tool_fn(name: str) -> Any:
    """Return the raw function behind a registered MCP tool."""
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None, f"tool {name!r} is not registered"
    return tool.fn


@pytest.mark.parametrize(
    ("tool_name", "endpoint"),
    [
        ("firewall_address_rename", "firewall/address"),
        ("firewall_addrgrp_rename", "firewall/addrgrp"),
    ],
)
@pytest.mark.asyncio
async def test_rename_issues_put_with_new_name(
    httpx_mock: HTTPXMock, tool_name: str, endpoint: str
) -> None:
    httpx_mock.add_response(
        method="PUT",
        url=f"https://fw.example.test/api/v2/cmdb/{endpoint}/old-name?vdom=root",
        json={"status": "success", "revision": "1"},
    )
    registry = build_registry(fw="https://fw.example.test")

    async with registry.get("fw"):
        result = await _tool_fn(tool_name)(
            ctx=build_ctx(registry),
            fortigate="fw",
            name="old-name",
            new_name="new-name",
        )

    assert result["status"] == "success"

    request = httpx_mock.get_request()
    assert request is not None
    assert request.method == "PUT"
    assert request.url.path == f"/api/v2/cmdb/{endpoint}/old-name"
    # Only the name is sent: a rename must not touch subnets, members or comments.
    assert json.loads(request.read()) == {"name": "new-name"}


@pytest.mark.parametrize(
    "tool_name", ["firewall_address_rename", "firewall_addrgrp_rename"]
)
@pytest.mark.asyncio
async def test_rename_to_same_name_is_rejected_without_a_request(
    httpx_mock: HTTPXMock, tool_name: str
) -> None:
    registry = build_registry(fw="https://fw.example.test")

    async with registry.get("fw"):
        result = await _tool_fn(tool_name)(
            ctx=build_ctx(registry),
            fortigate="fw",
            name="same-name",
            new_name="same-name",
        )

    assert "error" in result
    assert httpx_mock.get_requests() == []
