"""Tests for the multi-device inventory loader and registry."""

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

import devices as devices_module
from devices import (
    DeviceConfig,
    DeviceConfigError,
    DeviceRegistry,
    UnknownDeviceError,
    load_devices,
    resolve_default,
)
from fortios_client import FortiOSClient
from server import mcp

TWO_DEVICES: list[dict[str, Any]] = [
    {
        "name": "aef01",
        "host": "https://aef01.test:4443",
        "api_token": "token-a",
        "site": "aef",
        "default": True,
    },
    {
        "name": "bef01",
        "host": "https://bef01.test:4443",
        "api_token": "token-b",
        "site": "bef",
    },
]


@pytest.fixture(autouse=True)
def _clear_inventory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own .env out of these tests."""
    for var in (
        "FORTIOS_DEVICES_FILE",
        "FORTIOS_DEVICES",
        "FORTIOS_DEFAULT_DEVICE",
        "FORTIOS_HOST",
        "FORTIOS_API_TOKEN",
        "FORTIOS_VDOM",
        "FORTIOS_VERIFY_SSL",
        "FORTIOS_TIMEOUT",
        "FORTIOS_DEVICE_NAME",
    ):
        monkeypatch.delenv(var, raising=False)


# ── Loading ───────────────────────────────────────────────────────────


def test_yaml_file_inventory(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "devices.yaml"
    path.write_text(
        """
devices:
  - name: aef01
    host: https://aef01.test:4443
    api_token: ${AEF01_TOKEN}
    site: aef
    default: true
  - name: bef01
    host: https://bef01.test:4443
    api_token: token-b
    verify_ssl: true
    timeout: 45
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AEF01_TOKEN", "token-from-env")
    monkeypatch.setenv("FORTIOS_DEVICES_FILE", str(path))

    result = load_devices()

    assert [d.name for d in result] == ["aef01", "bef01"]
    # ${VAR} in api_token is expanded so tokens can stay out of the file.
    assert result[0].api_token == "token-from-env"
    assert result[0].site == "aef"
    assert result[1].verify_ssl is True
    assert result[1].timeout == 45
    assert resolve_default(result) == "aef01"


def test_inline_json_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORTIOS_DEVICES", json.dumps(TWO_DEVICES))

    result = load_devices()

    assert [d.name for d in result] == ["aef01", "bef01"]
    assert resolve_default(result) == "aef01"


def test_single_device_env_fallback_stays_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORTIOS_HOST", "https://fw.test")
    monkeypatch.setenv("FORTIOS_API_TOKEN", "token")
    monkeypatch.setenv("FORTIOS_VDOM", "vd1")
    monkeypatch.setenv("FORTIOS_VERIFY_SSL", "true")

    result = load_devices()

    assert len(result) == 1
    assert result[0].name == "default"
    assert result[0].vdom == "vd1"
    assert result[0].verify_ssl is True
    assert resolve_default(result) == "default"


def test_missing_configuration_names_every_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DeviceConfigError) as excinfo:
        load_devices()

    message = str(excinfo.value)
    assert "FORTIOS_DEVICES_FILE" in message
    assert "FORTIOS_DEVICES" in message
    assert "FORTIOS_HOST" in message


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"name": "a", "host": "aef01.test", "api_token": "t"}, "https://"),
        (
            {"name": "a", "host": "https://a.test", "api_token": "${NOPE_UNSET}"},
            "unset",
        ),
        ({"name": "a", "host": "https://a.test"}, "api_token"),
    ],
)
def test_invalid_device_is_rejected_with_its_name(
    monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any], expected: str
) -> None:
    monkeypatch.setenv("FORTIOS_DEVICES", json.dumps([entry]))

    with pytest.raises(DeviceConfigError) as excinfo:
        load_devices()

    assert "device a" in str(excinfo.value)
    assert expected in str(excinfo.value)


def test_duplicate_names_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    duplicated = TWO_DEVICES + [{**TWO_DEVICES[0], "default": False}]
    monkeypatch.setenv("FORTIOS_DEVICES", json.dumps(duplicated))

    with pytest.raises(DeviceConfigError, match="Duplicate device names.*aef01"):
        load_devices()


# ── Default selection ─────────────────────────────────────────────────


def test_env_override_wins_over_inventory_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORTIOS_DEFAULT_DEVICE", "bef01")
    result = [DeviceConfig(**d) for d in TWO_DEVICES]

    assert resolve_default(result) == "bef01"


def test_unknown_default_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORTIOS_DEFAULT_DEVICE", "nope")
    result = [DeviceConfig(**d) for d in TWO_DEVICES]

    with pytest.raises(DeviceConfigError, match="not in the inventory"):
        resolve_default(result)


def test_two_defaults_are_rejected() -> None:
    result = [DeviceConfig(**{**d, "default": True}) for d in TWO_DEVICES]

    with pytest.raises(DeviceConfigError, match="More than one device is marked"):
        resolve_default(result)


def test_no_default_falls_back_to_first_with_a_warning(caplog) -> None:
    result = [DeviceConfig(**{**d, "default": False}) for d in TWO_DEVICES]

    with caplog.at_level("WARNING", logger="fortios_mcp"):
        assert resolve_default(result) == "aef01"

    assert "No default device configured" in caplog.text


# ── Registry ──────────────────────────────────────────────────────────


def _registry() -> DeviceRegistry:
    registry = DeviceRegistry("aef01")
    for entry in TWO_DEVICES:
        config = DeviceConfig(**entry)
        registry.register(
            config, FortiOSClient(host=config.host, api_token=config.api_token)
        )
    return registry


def test_registry_resolves_names_and_default() -> None:
    registry = _registry()

    assert registry.names() == ["aef01", "bef01"]
    assert registry.get().host == "https://aef01.test:4443"
    assert registry.get("bef01").host == "https://bef01.test:4443"


def test_unknown_device_error_lists_valid_names() -> None:
    with pytest.raises(UnknownDeviceError) as excinfo:
        _registry().get("nope")

    assert "aef01" in str(excinfo.value)
    assert "bef01" in str(excinfo.value)


def test_select_by_site_and_unknown_site() -> None:
    registry = _registry()

    assert registry.select(site="aef") == ["aef01"]
    assert registry.select() == ["aef01", "bef01"]
    with pytest.raises(UnknownDeviceError, match="Known sites"):
        registry.select(site="nope")


def test_describe_never_exposes_tokens() -> None:
    described = _registry().describe()

    assert [d["name"] for d in described] == ["aef01", "bef01"]
    assert described[0]["is_default"] is True
    assert "token" not in json.dumps(described)


# ── Tool routing ──────────────────────────────────────────────────────


def _tool_fn(name: str) -> Any:
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None, f"tool {name!r} is not registered"
    return tool.fn


def _ctx(registry: DeviceRegistry) -> Any:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={"client": registry.get(), "devices": registry}
        ),
        error=_noop,
    )


async def _noop(*_: Any, **__: Any) -> None:
    return None


@pytest.mark.parametrize(
    ("fortigate", "expected_host"),
    [
        (None, "aef01.test"),
        ("aef01", "aef01.test"),
        ("bef01", "bef01.test"),
    ],
)
@pytest.mark.asyncio
async def test_fortigate_parameter_routes_to_the_right_firewall(
    httpx_mock: HTTPXMock, fortigate: str | None, expected_host: str
) -> None:
    httpx_mock.add_response(json={"status": "success", "results": []})
    registry = _registry()

    async with registry.get("aef01"), registry.get("bef01"):
        await _tool_fn("cmdb_list")(
            ctx=_ctx(registry), resource_path="firewall/address", fortigate=fortigate
        )

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.host == expected_host
    # Each device authenticates with its own token.
    expected_token = "token-a" if expected_host == "aef01.test" else "token-b"
    assert request.headers["Authorization"] == f"Bearer {expected_token}"


@pytest.mark.asyncio
async def test_unknown_device_returns_an_error_without_calling_out(
    httpx_mock: HTTPXMock,
) -> None:
    registry = _registry()

    result = await _tool_fn("cmdb_list")(
        ctx=_ctx(registry), resource_path="firewall/address", fortigate="nope"
    )

    assert "Unknown device" in result["error"]
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_devices_list_reports_inventory_without_tokens() -> None:
    result = await _tool_fn("fortios_devices_list")(ctx=_ctx(_registry()))

    assert result["default_device"] == "aef01"
    assert result["count"] == 2
    assert "token" not in json.dumps(result)


@pytest.mark.asyncio
async def test_devices_check_probes_every_device_and_isolates_failures(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=re.compile(r"https://aef01\.test.*"),
        json={
            "status": "success",
            "version": "v7.4.12",
            "serial": "FG1",
            "results": {"hostname": "aef01", "model_name": "FortiGate"},
        },
    )
    httpx_mock.add_response(url=re.compile(r"https://bef01\.test.*"), status_code=401)
    registry = _registry()

    async with registry.get("aef01"), registry.get("bef01"):
        result = await _tool_fn("fortios_devices_check")(ctx=_ctx(registry))

    assert result["checked"] == 2
    assert result["reachable"] == 1
    by_name = {r["device"]: r for r in result["results"]}
    assert by_name["aef01"]["version"] == "v7.4.12"
    assert by_name["bef01"]["reachable"] is False
    assert "error" in by_name["bef01"]


def test_module_exports_client_for_helper() -> None:
    assert callable(devices_module.client_for)


# ── Logging for unreachable / unauthorized devices ────────────────────


@pytest.mark.asyncio
async def test_unreachable_device_is_logged_and_does_not_break_the_call(
    httpx_mock: HTTPXMock, caplog
) -> None:
    import httpx

    from fortios_client import FortiOSError

    httpx_mock.add_exception(httpx.ConnectTimeout(""))

    async with FortiOSClient(
        host="https://down.test", api_token="token", name="bef01"
    ) as client:
        with caplog.at_level("WARNING", logger="fortios_client"):
            with pytest.raises(FortiOSError, match="Cannot reach https://down.test"):
                await client.monitor_get("system/status")

    # The device name is in the log so an operator can tell which firewall
    # stopped answering without reading tool responses.
    assert "bef01" in caplog.text
    assert "unreachable" in caplog.text
    assert "ConnectTimeout" in caplog.text


@pytest.mark.asyncio
async def test_permission_error_is_logged_as_a_warning(
    httpx_mock: HTTPXMock, caplog
) -> None:
    from fortios_client import FortiOSError

    httpx_mock.add_response(
        status_code=403, json={"http_status": 403, "status": "error"}
    )

    async with FortiOSClient(
        host="https://fw.test", api_token="token", name="aef01"
    ) as client:
        with caplog.at_level("WARNING", logger="fortios_client"):
            with pytest.raises(FortiOSError):
                await client.cmdb_get("firewall/policy")

    assert "aef01" in caplog.text
    assert "403" in caplog.text


@pytest.mark.asyncio
async def test_routine_api_errors_stay_out_of_the_warning_log(
    httpx_mock: HTTPXMock, caplog
) -> None:
    from fortios_client import FortiOSError

    httpx_mock.add_response(
        status_code=404, json={"http_status": 404, "status": "error"}
    )

    async with FortiOSClient(
        host="https://fw.test", api_token="token", name="aef01"
    ) as client:
        with caplog.at_level("WARNING", logger="fortios_client"):
            with pytest.raises(FortiOSError):
                await client.cmdb_get("firewall/address/nope")

    assert caplog.text == ""
