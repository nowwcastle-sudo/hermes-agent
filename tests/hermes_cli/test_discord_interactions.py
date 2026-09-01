from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from hermes_cli.discord_interactions import (
    DiscordInteractions,
    decode_custom_id,
    encode_custom_id,
    validate_interaction_result,
    validate_message_spec,
    validate_modal_spec,
)
from hermes_cli.plugin_capabilities import CAPABILITY_REGISTRY
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def _context(
    *,
    name: str = "cs-quiz",
    key: str = "",
    manager: PluginManager | None = None,
) -> PluginContext:
    return PluginContext(
        PluginManifest(name=name, key=key, source="user"),
        manager or PluginManager(),
    )


def valid_message_spec() -> dict:
    return {
        "api_version": 1,
        "content": "문제",
        "embeds": [],
        "components": [
            {
                "type": "button",
                "action": "submit_choice",
                "route_token": "A" * 24,
                "label": "1번",
                "style": "primary",
                "value": "0",
                "disabled": False,
            }
        ],
    }


def valid_modal_spec(style: str = "short", max_length: int = 300) -> dict:
    return {
        "api_version": 1,
        "action": "submit_answer",
        "title": "답변",
        "fields": [
            {
                "id": "answer",
                "label": "답변을 입력하세요",
                "style": style,
                "required": True,
                "min_length": 1,
                "max_length": max_length,
            }
        ],
    }


def _granted_adapter(monkeypatch) -> SimpleNamespace:
    adapter = SimpleNamespace(
        is_connected=True,
        plugin_interaction_send=AsyncMock(
            return_value={
                "ok": True,
                "channel_id": "10",
                "message_id": "20",
                "error_code": "",
            }
        ),
        plugin_interaction_update=AsyncMock(
            return_value={"ok": True, "error_code": ""}
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda *_: True,
    )
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref",
        lambda: SimpleNamespace(adapters={Platform.DISCORD: adapter}),
    )
    return adapter


@pytest.mark.asyncio
async def test_send_rechecks_capability_and_returns_normalized_receipt(
    monkeypatch,
) -> None:
    receipt = {
        "ok": True,
        "channel_id": "10",
        "message_id": "20",
        "error_code": "",
        "adapter_private": object(),
    }
    adapter = SimpleNamespace(
        is_connected=True,
        plugin_interaction_send=AsyncMock(return_value=receipt),
    )
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref",
        lambda: SimpleNamespace(adapters={Platform.DISCORD: adapter}),
    )
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda *_: True,
    )
    spec = valid_message_spec()

    result = await DiscordInteractions("cs-quiz").send("10", spec)

    assert result == {
        "ok": True,
        "channel_id": "10",
        "message_id": "20",
        "error_code": "",
    }
    adapter.plugin_interaction_send.assert_awaited_once_with("10", spec)
    assert result is not receipt


@pytest.mark.asyncio
async def test_update_revocation_fails_before_adapter(monkeypatch) -> None:
    adapter = SimpleNamespace(
        is_connected=True,
        plugin_interaction_update=AsyncMock(),
    )
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref",
        lambda: SimpleNamespace(adapters={Platform.DISCORD: adapter}),
    )
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda *_: False,
    )

    result = await DiscordInteractions("cs-quiz").update(
        "10", "20", valid_message_spec()
    )

    assert result == {"ok": False, "error_code": "capability_not_granted"}
    adapter.plugin_interaction_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_consent_read_failure_is_fail_closed(monkeypatch) -> None:
    def fail_consent_read(*_args):
        raise OSError("sensitive consent backend detail")

    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        fail_consent_read,
    )

    result = await DiscordInteractions("cs-quiz").send("10", valid_message_spec())

    assert result == {"ok": False, "error_code": "capability_not_granted"}


@pytest.mark.asyncio
async def test_send_gateway_unavailable_returns_stable_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda *_: True,
    )
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)

    result = await DiscordInteractions("cs-quiz").send("10", valid_message_spec())

    assert result == {"ok": False, "error_code": "gateway_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapters", "error_code"),
    [
        ({}, "adapter_not_registered"),
        (
            {Platform.DISCORD: SimpleNamespace(is_connected=False)},
            "adapter_disconnected",
        ),
    ],
)
async def test_send_adapter_absent_or_disconnected_returns_stable_error(
    monkeypatch, adapters: dict, error_code: str
) -> None:
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda *_: True,
    )
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref",
        lambda: SimpleNamespace(adapters=adapters),
    )

    result = await DiscordInteractions("cs-quiz").send("10", valid_message_spec())

    assert result == {"ok": False, "error_code": error_code}


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [None, 10, "", " ", "-1", "1.0", "１２"])
async def test_send_rejects_invalid_channel_id_before_adapter(
    monkeypatch, channel_id: object
) -> None:
    adapter = _granted_adapter(monkeypatch)

    result = await DiscordInteractions("cs-quiz").send(
        channel_id, valid_message_spec()  # type: ignore[arg-type]
    )

    assert result == {"ok": False, "error_code": "invalid_argument"}
    adapter.plugin_interaction_send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_id", "message_id"),
    [("x", "20"), ("10", None), ("10", 20), ("10", ""), ("10", "2 0")],
)
async def test_update_rejects_invalid_ids_before_adapter(
    monkeypatch, channel_id: object, message_id: object
) -> None:
    adapter = _granted_adapter(monkeypatch)

    result = await DiscordInteractions("cs-quiz").update(
        channel_id,  # type: ignore[arg-type]
        message_id,  # type: ignore[arg-type]
        valid_message_spec(),
    )

    assert result == {"ok": False, "error_code": "invalid_argument"}
    adapter.plugin_interaction_update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["send", "update"])
async def test_send_and_update_reject_invalid_spec_without_raising(
    monkeypatch, verb: str
) -> None:
    adapter = _granted_adapter(monkeypatch)
    interactions = DiscordInteractions("cs-quiz")
    invalid_spec = valid_message_spec()
    invalid_spec["api_version"] = 2

    if verb == "send":
        result = await interactions.send("10", invalid_spec)
    else:
        result = await interactions.update("10", "20", invalid_spec)

    assert result == {"ok": False, "error_code": "invalid_argument"}
    adapter.plugin_interaction_send.assert_not_awaited()
    adapter.plugin_interaction_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_calls_only_interaction_update_and_normalizes_success(
    monkeypatch,
) -> None:
    adapter = _granted_adapter(monkeypatch)
    receipt = {"ok": True, "error_code": "", "sdk_message": object()}
    adapter.plugin_interaction_update.return_value = receipt
    spec = valid_message_spec()

    result = await DiscordInteractions("cs-quiz").update("10", "20", spec)

    assert result == {"ok": True, "error_code": ""}
    adapter.plugin_interaction_update.assert_awaited_once()
    channel_id, message_id, normalized = adapter.plugin_interaction_update.await_args.args
    assert (channel_id, message_id) == ("10", "20")
    assert normalized == spec
    assert normalized is not spec
    adapter.plugin_interaction_send.assert_not_awaited()
    assert result is not receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["send", "update"])
async def test_adapter_failure_receipt_is_normalized(monkeypatch, verb: str) -> None:
    adapter = _granted_adapter(monkeypatch)
    receipt = {
        "ok": False,
        "error_code": "forbidden",
        "exception": RuntimeError("private SDK detail"),
    }
    adapter.plugin_interaction_send.return_value = receipt
    adapter.plugin_interaction_update.return_value = receipt
    interactions = DiscordInteractions("cs-quiz")

    if verb == "send":
        result = await interactions.send("10", valid_message_spec())
    else:
        result = await interactions.update("10", "20", valid_message_spec())

    assert result == {"ok": False, "error_code": "forbidden"}
    assert result is not receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {},
        {"ok": 1, "error_code": ""},
        {"ok": True, "channel_id": "x", "message_id": "20", "error_code": ""},
        {"ok": True, "channel_id": "10", "message_id": 20, "error_code": ""},
        {"ok": False, "error_code": ""},
    ],
)
async def test_send_rejects_malformed_adapter_receipt(
    monkeypatch, receipt: object
) -> None:
    adapter = _granted_adapter(monkeypatch)
    adapter.plugin_interaction_send.return_value = receipt

    result = await DiscordInteractions("cs-quiz").send("10", valid_message_spec())

    assert result == {"ok": False, "error_code": "invalid_adapter_receipt"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {},
        {"ok": 1, "error_code": ""},
        {"ok": True, "error_code": "unexpected"},
        {"ok": False, "error_code": ""},
    ],
)
async def test_update_rejects_malformed_adapter_receipt(
    monkeypatch, receipt: object
) -> None:
    adapter = _granted_adapter(monkeypatch)
    adapter.plugin_interaction_update.return_value = receipt

    result = await DiscordInteractions("cs-quiz").update(
        "10", "20", valid_message_spec()
    )

    assert result == {"ok": False, "error_code": "invalid_adapter_receipt"}


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["send", "update"])
async def test_adapter_exception_returns_stable_error_and_logs_only_trace_id(
    monkeypatch, caplog, verb: str
) -> None:
    adapter = _granted_adapter(monkeypatch)
    sensitive_text = "API token=do-not-expose"
    adapter.plugin_interaction_send.side_effect = RuntimeError(sensitive_text)
    adapter.plugin_interaction_update.side_effect = RuntimeError(sensitive_text)
    interactions = DiscordInteractions("cs-quiz")

    with caplog.at_level(logging.ERROR, logger="hermes_cli.discord_interactions"):
        if verb == "send":
            result = await interactions.send("10", valid_message_spec())
        else:
            result = await interactions.update("10", "20", valid_message_spec())

    assert result == {"ok": False, "error_code": "adapter_error"}
    assert sensitive_text not in str(result)
    assert sensitive_text not in caplog.text
    assert re.search(r"trace_id=[0-9a-f]{32}\b", caplog.text)


def test_plugin_context_discord_is_cached_and_bound_to_canonical_id() -> None:
    ctx = _context(name="display-name", key="cs-quiz")

    facade = ctx.discord

    assert isinstance(facade, DiscordInteractions)
    assert facade._plugin_id == "cs-quiz"
    assert ctx.discord is facade


def test_custom_id_round_trip_and_limit() -> None:
    custom_id = encode_custom_id("cs-quiz", "submit_choice", "A" * 32)

    assert len(custom_id) <= 90
    assert decode_custom_id(custom_id) == {
        "plugin_id": "cs-quiz",
        "action": "submit_choice",
        "route_token": "A" * 32,
    }


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "other:cs-quiz:x:y",
        "hdi1:bad",
        "hdi1:::x",
        "hdi1.YQ===.submit_choice." + "A" * 24,
        "hdi1._w.submit_choice." + "A" * 24,
        "hdi1.Y3MtcXVpeg.SubmitChoice." + "A" * 24,
        "hdi1.Y3MtcXVpeg.submit_choice.short",
        "hdi1.Y3MtcXVpeg.submit_choice." + "A" * 44,
        "hdi1.Y3MtcXVpeg.submit_choice." + "A" * 24 + ".extra",
    ],
)
def test_custom_id_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError, match="custom ID"):
        decode_custom_id(raw)


@pytest.mark.parametrize(
    ("plugin_id", "action", "route_token"),
    [
        ("", "submit_choice", "A" * 24),
        ("cs-quiz", "SubmitChoice", "A" * 24),
        ("cs-quiz", "a" * 33, "A" * 24),
        ("cs-quiz", "submit_choice", "short"),
        ("cs-quiz", "submit_choice", "A" * 44),
        ("long-plugin", "a" * 32, "A" * 43),
        ("cs-quiz", None, "A" * 24),
        ("cs-quiz", "submit_choice", None),
    ],
)
def test_custom_id_encoder_rejects_invalid_or_over_budget_fields(
    plugin_id: str, action: str, route_token: str
) -> None:
    with pytest.raises(ValueError, match="custom ID"):
        encode_custom_id(plugin_id, action, route_token)


def test_message_spec_returns_independent_json_copy() -> None:
    spec = valid_message_spec()

    normalized = validate_message_spec(spec)
    normalized["components"][0]["label"] = "changed"

    assert spec == valid_message_spec()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda spec: spec.update(extra=True),
        lambda spec: spec.update(api_version=2),
        lambda spec: spec.update(content=1),
        lambda spec: spec.update(embeds={}),
        lambda spec: spec.update(components={}),
        lambda spec: spec["components"][0].update(type="select"),
        lambda spec: spec["components"][0].update(custom_id="plugin-owned"),
        lambda spec: spec["components"][0].update(action="SubmitChoice"),
        lambda spec: spec["components"][0].update(route_token="short"),
        lambda spec: spec["components"][0].update(label="x" * 71),
        lambda spec: spec["components"][0].update(style="link"),
        lambda spec: spec["components"][0].update(value=0),
        lambda spec: spec["components"][0].update(disabled=0),
    ],
)
def test_message_spec_rejects_invalid_schema_and_button_budget(mutate) -> None:
    spec = valid_message_spec()
    mutate(spec)

    with pytest.raises(ValueError, match="message spec"):
        validate_message_spec(spec)


@pytest.mark.parametrize("style", [[], {}])
def test_message_spec_rejects_non_string_button_style(style: object) -> None:
    spec = valid_message_spec()
    spec["components"][0]["style"] = style

    with pytest.raises(ValueError, match="message spec"):
        validate_message_spec(spec)


def test_modal_spec_accepts_short_and_paragraph_budgets_without_mutation() -> None:
    short = valid_modal_spec()
    paragraph = valid_modal_spec("paragraph", 2_000)

    assert validate_modal_spec(short) == short
    assert validate_modal_spec(paragraph) == paragraph
    assert validate_modal_spec(short) is not short


@pytest.mark.parametrize(
    "mutate",
    [
        lambda spec: spec.update(extra=True),
        lambda spec: spec.update(api_version=2),
        lambda spec: spec.update(action="SubmitAnswer"),
        lambda spec: spec.update(title=""),
        lambda spec: spec.update(title="x" * 46),
        lambda spec: spec.update(fields={}),
        lambda spec: spec["fields"][0].update(extra=True),
        lambda spec: spec["fields"][0].update(id=""),
        lambda spec: spec["fields"][0].update(label="x" * 46),
        lambda spec: spec["fields"][0].update(style="long"),
        lambda spec: spec["fields"][0].update(required=1),
        lambda spec: spec["fields"][0].update(min_length=-1),
        lambda spec: spec["fields"][0].update(min_length=2, max_length=1),
        lambda spec: spec["fields"][0].update(max_length=301),
        lambda spec: spec["fields"][0].update(style="paragraph", max_length=2_001),
    ],
)
def test_modal_spec_rejects_invalid_schema_and_discord_budgets(mutate) -> None:
    spec = valid_modal_spec()
    mutate(spec)

    with pytest.raises(ValueError, match="Modal spec"):
        validate_modal_spec(spec)


@pytest.mark.parametrize("style", [[], {}])
def test_modal_spec_rejects_non_string_field_style(style: object) -> None:
    spec = valid_modal_spec()
    spec["fields"][0]["style"] = style

    with pytest.raises(ValueError, match="Modal spec"):
        validate_modal_spec(spec)


def test_open_modal_is_button_only() -> None:
    result = {
        "kind": "open_modal",
        "modal": {"api_version": 1, "action": "submit", "title": "답변", "fields": []},
    }

    with pytest.raises(ValueError, match="button"):
        validate_interaction_result(result, interaction_kind="modal_submit")


@pytest.mark.parametrize(
    ("result", "interaction_kind"),
    [
        (
            {
                "kind": "open_modal",
                "modal": {
                    "api_version": 1,
                    "action": "submit",
                    "title": "답변",
                    "fields": [],
                },
            },
            "button",
        ),
        ({"kind": "update_message", "message": valid_message_spec()}, "button"),
        ({"kind": "ephemeral", "content": "안내"}, "modal_submit"),
        ({"kind": "no_change"}, "modal_submit"),
    ],
)
def test_interaction_result_accepts_each_version_one_variant(
    result: dict, interaction_kind: str
) -> None:
    normalized = validate_interaction_result(result, interaction_kind=interaction_kind)

    assert normalized == result
    assert normalized is not result


@pytest.mark.parametrize(
    ("result", "interaction_kind"),
    [
        ({"kind": "unknown"}, "button"),
        ({"kind": "no_change", "extra": True}, "button"),
        ({"kind": "open_modal", "message": valid_message_spec()}, "button"),
        ({"kind": "update_message", "modal": valid_modal_spec()}, "button"),
        ({"kind": "ephemeral", "content": "x" * 2_001}, "modal_submit"),
        ({"kind": "no_change"}, "select"),
    ],
)
def test_interaction_result_rejects_unknown_keys_payloads_and_kinds(
    result: dict, interaction_kind: str
) -> None:
    with pytest.raises(ValueError, match="interaction result"):
        validate_interaction_result(result, interaction_kind=interaction_kind)


@pytest.mark.parametrize("kind", [[], {}])
def test_interaction_result_rejects_non_string_kind(kind: object) -> None:
    with pytest.raises(ValueError, match="interaction result"):
        validate_interaction_result({"kind": kind}, interaction_kind="button")


def test_discord_interactions_capability_is_registered() -> None:
    spec = CAPABILITY_REGISTRY["gateway.discord_interactions"]

    assert spec.legacy_path == ("allow_discord_interactions",)
    assert "Discord" in spec.description


def test_registration_is_default_off(monkeypatch) -> None:
    ctx = _context()
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: False,
    )

    with pytest.raises(PermissionError, match="gateway.discord_interactions"):
        ctx.register_discord_interaction(lambda interaction: {"kind": "no_change"})


def test_registration_rejects_non_callable_handler(monkeypatch) -> None:
    ctx = _context()
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )

    with pytest.raises(TypeError, match="callable"):
        ctx.register_discord_interaction(None)  # type: ignore[arg-type]


def test_registration_uses_canonical_plugin_id_and_rejects_collision(
    monkeypatch,
) -> None:
    manager = PluginManager()
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )
    first = _context(name="display-one", key="cs-quiz", manager=manager)
    second = _context(name="display-two", key="cs-quiz", manager=manager)
    handler = lambda interaction: {"kind": "no_change"}

    first.register_discord_interaction(handler)

    assert manager.get_discord_interaction_handler("cs-quiz") is handler
    assert manager.get_discord_interaction_handler("display-one") is None
    with pytest.raises(ValueError, match="already registered"):
        second.register_discord_interaction(
            lambda interaction: {"kind": "no_change"}
        )


def test_registration_is_manager_local(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )
    first_manager = PluginManager(scope_key="first-profile")
    second_manager = PluginManager(scope_key="second-profile")
    first_handler = lambda interaction: {"kind": "no_change"}
    second_handler = lambda interaction: {"kind": "no_change"}

    _context(manager=first_manager).register_discord_interaction(first_handler)
    _context(manager=second_manager).register_discord_interaction(second_handler)

    assert first_manager.get_discord_interaction_handler("cs-quiz") is first_handler
    assert second_manager.get_discord_interaction_handler("cs-quiz") is second_handler


def test_registration_dispose_removes_only_identical_handler(monkeypatch) -> None:
    manager = PluginManager()
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )
    handler = lambda interaction: {"kind": "no_change"}
    replacement = lambda interaction: {"kind": "no_change"}
    handle = _context(manager=manager).register_discord_interaction(handler)

    manager._discord_interaction_handlers["cs-quiz"] = replacement
    handle.dispose()

    assert handle.active is False
    assert manager.get_discord_interaction_handler("cs-quiz") is replacement
    assert manager._ownership_ledger == {}


def test_targeted_unload_releases_discord_interaction_registration(
    monkeypatch,
) -> None:
    manager = PluginManager()
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )
    handle = _context(manager=manager).register_discord_interaction(
        lambda interaction: {"kind": "no_change"}
    )

    assert manager.unload("cs-quiz") is True
    assert handle.active is False
    assert manager.get_discord_interaction_handler("cs-quiz") is None
    assert manager._ownership_ledger == {}


def test_full_unload_clears_discord_interaction_lifecycle_state(monkeypatch) -> None:
    manager = PluginManager()
    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )
    _context(name="first", manager=manager).register_discord_interaction(
        lambda interaction: {"kind": "no_change"}
    )
    _context(name="second", manager=manager).register_discord_interaction(
        lambda interaction: {"kind": "no_change"}
    )

    assert manager.unload() is True
    assert manager.get_discord_interaction_handler("first") is None
    assert manager.get_discord_interaction_handler("second") is None
    assert manager._discord_interaction_handlers == {}
    assert manager._registration_order == []
