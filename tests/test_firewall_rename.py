"""Tests for the firewall address / address group rename tools."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from fortios_client import FortiOSClient
from server import mcp


def _tool_fn(name: str) -> Any:
    """Return the raw function behind a registered MCP tool."""
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None, f"tool {name!r} is not registered"
    return tool.fn


def _ctx(client: FortiOSClient) -> Any:
    """Minimal stand-in for the MCP Context the tools read the client from."""
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"client": client})
    )


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

    async with FortiOSClient(
        host="https://fw.example.test", api_token="token", vdom="root"
    ) as client:
        result = await _tool_fn(tool_name)(
            ctx=_ctx(client), name="old-name", new_name="new-name"
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
    async with FortiOSClient(
        host="https://fw.example.test", api_token="token", vdom="root"
    ) as client:
        result = await _tool_fn(tool_name)(
            ctx=_ctx(client), name="same-name", new_name="same-name"
        )

    assert "error" in result
    assert httpx_mock.get_requests() == []
