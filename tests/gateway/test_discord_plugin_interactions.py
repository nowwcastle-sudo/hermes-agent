"""Discord rendering and REST I/O for plugin-owned interactions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from hermes_cli.discord_interactions import (
    DiscordInteractions,
    decode_custom_id,
    encode_custom_id,
)
from plugins.platforms.discord.adapter import DiscordAdapter
import plugins.platforms.discord.adapter as discord_adapter_module
from plugins.platforms.discord.plugin_interactions import (
    DiscordPluginInteractionBridge,
)


def _message_spec() -> dict:
    return {
        "api_version": 1,
        "content": "Choose one",
        "embeds": [
            {
                "title": "Question",
                "description": "What is the result?",
                "color": 0x123456,
                "fields": [
                    {"name": "Topic", "value": "Caching", "inline": True},
                ],
                "accepted_answers": ["secret"],
                "rubric": {"secret": True},
                "unknown": "ignored",
            }
        ],
        "components": [
            {
                "type": "button",
                "action": "submit_choice",
                "route_token": "A" * 24,
                "label": "Choice A",
                "style": "success",
                "value": "choice-a",
                "disabled": True,
            }
        ],
    }


def _fake_interaction(custom_id: str) -> SimpleNamespace:
    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
        send_modal=AsyncMock(),
        defer=AsyncMock(),
    )
    return SimpleNamespace(
        id=101,
        type=discord_adapter_module.discord.InteractionType.component,
        data={"custom_id": custom_id, "component_type": 2},
        user=SimpleNamespace(id=202, roles=[]),
        guild_id=303,
        channel_id=404,
        message=SimpleNamespace(id=505),
        response=response,
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def _fake_modal_submit(custom_id: str, components: list) -> SimpleNamespace:
    interaction = _fake_interaction(custom_id)
    interaction.type = discord_adapter_module.discord.InteractionType.modal_submit
    interaction.data = {"custom_id": custom_id, "components": components}
    return interaction


class _FakeModal:
    def __init__(self, *, title: str, custom_id: str):
        self.title = title
        self.custom_id = custom_id
        self.children = []

    def add_item(self, item) -> None:
        self.children.append(item)


class _FakeTextInput:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _allow_handler(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions._component_check_auth",
        lambda *_: True,
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions.get_plugin_manager",
        lambda: SimpleNamespace(get_discord_interaction_handler=lambda _: handler),
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions.plugin_capability_granted",
        lambda *_: True,
    )
    monkeypatch.setattr(
        discord_adapter_module.discord.ui, "Modal", _FakeModal, raising=False
    )
    monkeypatch.setattr(
        discord_adapter_module.discord.ui, "TextInput", _FakeTextInput, raising=False
    )
    monkeypatch.setattr(
        discord_adapter_module.discord,
        "TextStyle",
        SimpleNamespace(short="short", paragraph="paragraph"),
        raising=False,
    )


def _modal_result(action: str = "submit_answer") -> dict:
    return {
        "kind": "open_modal",
        "modal": {
            "api_version": 1,
            "action": action,
            "title": "Answer",
            "fields": [
                {
                    "id": "answer",
                    "label": "Your answer",
                    "style": "short",
                    "required": True,
                    "min_length": 1,
                    "max_length": 300,
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_non_owned_custom_id_returns_false_without_ack_or_callback() -> None:
    bridge = DiscordPluginInteractionBridge(adapter=SimpleNamespace())
    interaction = _fake_interaction("foreign.button")

    owned = await bridge.handle_interaction(interaction)

    assert owned is False
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_malformed_custom_id_gets_one_bounded_ephemeral_ack() -> None:
    bridge = DiscordPluginInteractionBridge(adapter=SimpleNamespace())
    interaction = _fake_interaction("hdi1.malformed")

    owned = await bridge.handle_interaction(interaction)

    assert owned is True
    interaction.response.is_done.assert_called_once_with()
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert 1 <= len(interaction.response.send_message.await_args.args[0]) <= 200
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()


@pytest.mark.asyncio
async def test_unauthorized_owned_interaction_is_acked_without_callback(
    monkeypatch,
) -> None:
    handler = AsyncMock(return_value={"kind": "no_change"})
    adapter = SimpleNamespace(_allowed_user_ids=set(), _allowed_role_ids=set())
    bridge = DiscordPluginInteractionBridge(adapter=adapter)
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit", "A" * 16)
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions._component_check_auth",
        lambda *_: False,
        raising=False,
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions.get_plugin_manager",
        lambda: SimpleNamespace(get_discord_interaction_handler=lambda _: handler),
        raising=False,
    )

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    handler.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_present", "capability_granted"),
    [(False, True), (True, False)],
)
async def test_missing_or_revoked_handler_fails_closed_before_callback(
    monkeypatch,
    handler_present: bool,
    capability_granted: bool,
) -> None:
    handler = AsyncMock(return_value={"kind": "no_change"})
    adapter = SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    bridge = DiscordPluginInteractionBridge(adapter=adapter)
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit", "A" * 16)
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions._component_check_auth",
        lambda *_: True,
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions.get_plugin_manager",
        lambda: SimpleNamespace(
            get_discord_interaction_handler=lambda _: handler if handler_present else None
        ),
    )
    monkeypatch.setattr(
        "plugins.platforms.discord.plugin_interactions.plugin_capability_granted",
        lambda *_: capability_granted,
        raising=False,
    )

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    handler.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_prefix_button_sends_modal_without_defer(monkeypatch) -> None:
    handler = AsyncMock(return_value=_modal_result())
    adapter = SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    bridge = DiscordPluginInteractionBridge(adapter=adapter)
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_anything", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_not_awaited()
    handler.assert_awaited_once()
    assert handler.await_args.args[0]["action"] == "open_anything"
    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord_adapter_module.discord.ui.Modal)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_id_inherits_host_route_and_payload_keeps_component_value(
    monkeypatch,
) -> None:
    result = _modal_result(action="submit_inherited")
    result["modal"]["fields"].append(
        {
            "id": "details",
            "label": "Details",
            "style": "paragraph",
            "required": False,
            "min_length": 0,
            "max_length": 2_000,
        }
    )
    handler = AsyncMock(return_value=result)
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("canonical-plugin", "open_form", "R" * 24, "choice-z")
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    payload = handler.await_args.args[0]
    assert payload["component_value"] == "choice-z"
    modal = interaction.response.send_modal.await_args.args[0]
    assert decode_custom_id(modal.custom_id) == {
        "plugin_id": "canonical-plugin",
        "action": "submit_inherited",
        "route_token": "R" * 24,
    }
    assert [(field.custom_id, field.max_length) for field in modal.children] == [
        ("answer", 300),
        ("details", 2_000),
    ]


@pytest.mark.asyncio
async def test_non_open_button_defers_before_handler_then_updates(monkeypatch) -> None:
    events = []

    async def handler(payload):
        events.append(("handler", payload["kind"]))
        return {"kind": "update_message", "message": _message_spec()}

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    interaction.response.defer.side_effect = lambda: events.append(("defer", None))
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    assert events == [("defer", None), ("handler", "button")]
    interaction.response.defer.assert_awaited_once_with()
    interaction.edit_original_response.assert_awaited_once()
    assert set(interaction.edit_original_response.await_args.kwargs) == {
        "content",
        "embeds",
        "view",
    }
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_submit_defers_before_handler_with_nested_text_values(
    monkeypatch,
) -> None:
    events = []

    async def handler(payload):
        events.append(("handler", payload["kind"]))
        assert payload["modal_values"] == {
            "answer": "cache invalidation",
            "details": "nested input",
        }
        return {"kind": "no_change"}

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_modal_submit(
        encode_custom_id("plugin-one", "submit_answer", "A" * 16),
        [
            {
                "type": 1,
                "components": [
                    {"type": 4, "custom_id": "answer", "value": "cache invalidation"}
                ],
            },
            {
                "type": 1,
                "components": [
                    {"type": 4, "custom_id": "details", "value": "nested input"}
                ],
            },
        ],
    )
    interaction.response.defer.side_effect = lambda: events.append(("defer", None))
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    assert events == [("defer", None), ("handler", "modal_submit")]
    interaction.response.defer.assert_awaited_once_with()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_submit_ignores_malformed_nested_components(monkeypatch) -> None:
    handler = AsyncMock(return_value={"kind": "no_change"})
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_modal_submit(
        encode_custom_id("plugin-one", "submit_answer", "A" * 16),
        [
            None,
            {"components": None},
            {"components": [SimpleNamespace(custom_id="sdk", value="object")]},
            {"components": [{"custom_id": 123, "value": "number-id"}]},
            {"components": [{"custom_id": "number-value", "value": 456}]},
            {"components": [{"custom_id": "answer", "value": "kept"}]},
        ],
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    assert handler.await_args.args[0]["modal_values"] == {"answer": "kept"}


@pytest.mark.asyncio
async def test_button_and_modal_payloads_have_exact_json_v1_fields(monkeypatch) -> None:
    payloads = []

    async def handler(payload):
        payloads.append(payload)
        return {"kind": "no_change"}

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    button = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16, "choice-a")
    )
    modal = _fake_modal_submit(
        encode_custom_id("plugin-one", "submit_answer", "B" * 16),
        [{"components": [{"custom_id": "answer", "value": "two"}]}],
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(button) is True
    assert await bridge.handle_interaction(modal) is True

    assert payloads == [
        {
            "api_version": 1,
            "interaction_id": "101",
            "kind": "button",
            "plugin_id": "plugin-one",
            "action": "submit_choice",
            "route_token": "A" * 16,
            "user_id": "202",
            "guild_id": "303",
            "channel_id": "404",
            "message_id": "505",
            "component_value": "choice-a",
            "modal_values": {},
        },
        {
            "api_version": 1,
            "interaction_id": "101",
            "kind": "modal_submit",
            "plugin_id": "plugin-one",
            "action": "submit_answer",
            "route_token": "B" * 16,
            "user_id": "202",
            "guild_id": "303",
            "channel_id": "404",
            "message_id": "505",
            "modal_values": {"answer": "two"},
        },
    ]
    assert all(json.dumps(payload) for payload in payloads)


@pytest.mark.asyncio
async def test_deferred_modal_submit_rejects_open_modal_without_second_ack(
    monkeypatch,
) -> None:
    handler = AsyncMock(return_value=_modal_result())
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_modal_submit(
        encode_custom_id("plugin-one", "submit_answer", "A" * 16), []
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_awaited_once_with()
    handler.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    args = interaction.followup.send.await_args.args
    kwargs = interaction.followup.send.await_args.kwargs
    assert 1 <= len(args[0]) <= 200
    assert kwargs == {"ephemeral": True}


@pytest.mark.asyncio
async def test_unknown_interaction_defer_failure_stops_before_callback(
    monkeypatch,
) -> None:
    class UnknownInteraction(Exception):
        status = 404
        code = 10062

    handler = AsyncMock(return_value={"kind": "no_change"})
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    interaction.response.defer.side_effect = UnknownInteraction("expired")
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_awaited_once_with()
    handler.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


def test_renderer_uses_canonical_namespace_and_only_approved_native_fields() -> None:
    bridge = DiscordPluginInteractionBridge(adapter=None)

    kwargs = bridge.build_message_kwargs("cs-quiz", _message_spec())

    assert set(kwargs) == {"content", "embeds", "view"}
    assert kwargs["content"] == "Choose one"
    assert len(kwargs["embeds"]) == 1
    embed = kwargs["embeds"][0]
    assert embed.title == "Question"
    assert embed.description == "What is the result?"
    assert embed.color == 0x123456
    assert embed.fields == [{"name": "Topic", "value": "Caching", "inline": True}]
    assert not hasattr(embed, "accepted_answers")
    assert not hasattr(embed, "rubric")
    assert not hasattr(embed, "unknown")

    view = kwargs["view"]
    assert view.timeout is None
    assert len(view.children) == 1
    button = view.children[0]
    assert button.label == "Choice A"
    assert button.style == discord_adapter_module.discord.ButtonStyle.success
    assert button.disabled is True
    assert not hasattr(button, "value")
    assert button.callback is None
    assert decode_custom_id(button.custom_id) == {
        "plugin_id": "cs-quiz",
        "action": "submit_choice",
        "route_token": "A" * 24,
        "component_value": "choice-a",
    }
    visible = " ".join(
        [kwargs["content"], embed.title, embed.description, button.label]
    )
    assert "cs-quiz" not in visible
    assert "A" * 24 not in visible
    assert "secret" not in visible


@pytest.mark.asyncio
async def test_send_uses_cached_channel_and_returns_exact_receipt() -> None:
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    sent_message = SimpleNamespace(id=321)
    channel = SimpleNamespace(id=123, send=AsyncMock(return_value=sent_message))
    get_channel = MagicMock(return_value=channel)
    fetch_channel = AsyncMock()
    adapter._client = SimpleNamespace(
        get_channel=get_channel,
        fetch_channel=fetch_channel,
    )

    result = await adapter.plugin_interaction_send("cs-quiz", "123", _message_spec())

    assert result == {
        "ok": True,
        "channel_id": "123",
        "message_id": "321",
        "error_code": "",
    }
    get_channel.assert_called_once_with(123)
    fetch_channel.assert_not_awaited()
    channel.send.assert_awaited_once()
    assert set(channel.send.await_args.kwargs) == {"content", "embeds", "view"}


@pytest.mark.asyncio
async def test_send_fetches_channel_when_sdk_cache_is_empty() -> None:
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    sent_message = SimpleNamespace(id=654)
    channel = SimpleNamespace(id=123, send=AsyncMock(return_value=sent_message))
    get_channel = MagicMock(return_value=None)
    fetch_channel = AsyncMock(return_value=channel)
    adapter._client = SimpleNamespace(
        get_channel=get_channel,
        fetch_channel=fetch_channel,
    )

    result = await adapter.plugin_interaction_send("cs-quiz", "123", _message_spec())

    assert result == {
        "ok": True,
        "channel_id": "123",
        "message_id": "654",
        "error_code": "",
    }
    get_channel.assert_called_once_with(123)
    fetch_channel.assert_awaited_once_with(123)
    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_fetches_channel_and_message_with_empty_cache() -> None:
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    message = SimpleNamespace(edit=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    get_channel = MagicMock(return_value=None)
    fetch_channel = AsyncMock(return_value=channel)
    adapter._client = SimpleNamespace(
        get_channel=get_channel,
        fetch_channel=fetch_channel,
    )

    result = await adapter.plugin_interaction_update(
        "cs-quiz", "123", "456", _message_spec()
    )

    assert result == {"ok": True, "error_code": ""}
    get_channel.assert_called_once_with(123)
    fetch_channel.assert_awaited_once_with(123)
    channel.fetch_message.assert_awaited_once_with(456)
    message.edit.assert_awaited_once()
    assert set(message.edit.await_args.kwargs) == {"content", "embeds", "view"}
    button = message.edit.await_args.kwargs["view"].children[0]
    assert decode_custom_id(button.custom_id)["plugin_id"] == "cs-quiz"


class _DiscordNotFound(Exception):
    pass


class _DiscordForbidden(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "update"])
async def test_invalid_ids_or_specs_return_exact_invalid_argument(
    operation: str,
) -> None:
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    channel = SimpleNamespace(
        send=AsyncMock(),
        fetch_message=AsyncMock(),
    )
    adapter._client = SimpleNamespace(
        get_channel=MagicMock(return_value=channel),
        fetch_channel=AsyncMock(),
    )

    if operation == "send":
        invalid_spec = _message_spec()
        invalid_spec["embeds"] = ["not-an-embed"]
        result = await adapter.plugin_interaction_send(
            "cs-quiz", "123", invalid_spec
        )
    else:
        result = await adapter.plugin_interaction_update(
            "cs-quiz", "123", "not-a-message-id", _message_spec()
        )

    assert result == {"ok": False, "error_code": "invalid_argument"}
    channel.send.assert_not_awaited()
    channel.fetch_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "raised", "expected_code"),
    [
        ("send", _DiscordNotFound("missing channel"), "channel_not_found"),
        ("send", _DiscordForbidden("private"), "forbidden"),
        ("send", RuntimeError("private SDK detail"), "discord_error"),
        ("update", _DiscordNotFound("missing message"), "message_not_found"),
        ("update", _DiscordForbidden("private"), "forbidden"),
        ("update", RuntimeError("private SDK detail"), "discord_error"),
    ],
)
async def test_discord_failures_return_exact_safe_envelopes(
    caplog,
    monkeypatch,
    operation: str,
    raised: Exception,
    expected_code: str,
) -> None:
    monkeypatch.setattr(discord_adapter_module.discord, "NotFound", _DiscordNotFound)
    monkeypatch.setattr(discord_adapter_module.discord, "Forbidden", _DiscordForbidden)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    if operation == "send":
        channel = SimpleNamespace(send=AsyncMock(side_effect=raised))
    else:
        message = SimpleNamespace(edit=AsyncMock(side_effect=raised))
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    adapter._client = SimpleNamespace(
        get_channel=MagicMock(return_value=channel),
        fetch_channel=AsyncMock(),
    )

    if operation == "send":
        result = await adapter.plugin_interaction_send(
            "cs-quiz", "123", _message_spec()
        )
    else:
        result = await adapter.plugin_interaction_update(
            "cs-quiz", "123", "456", _message_spec()
        )

    assert result == {"ok": False, "error_code": expected_code}
    assert "private SDK detail" not in str(result)
    assert "private SDK detail" not in caplog.text


@pytest.mark.asyncio
async def test_facade_calls_real_adapter_with_corrected_plugin_id_signatures(
    monkeypatch,
) -> None:
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    sent_message = SimpleNamespace(id=321)
    existing_message = SimpleNamespace(edit=AsyncMock())
    channel = SimpleNamespace(
        send=AsyncMock(return_value=sent_message),
        fetch_message=AsyncMock(return_value=existing_message),
    )
    adapter._client = SimpleNamespace(
        get_channel=MagicMock(return_value=channel),
        fetch_channel=AsyncMock(),
    )
    interactions = DiscordInteractions("canonical-plugin")
    monkeypatch.setattr(interactions, "_capability_granted", lambda: True)
    monkeypatch.setattr(
        interactions,
        "_resolve_adapter",
        lambda: (adapter, None),
    )

    send_result = await interactions.send("123", _message_spec())
    update_result = await interactions.update("123", "456", _message_spec())

    assert send_result == {
        "ok": True,
        "channel_id": "123",
        "message_id": "321",
        "error_code": "",
    }
    assert update_result == {"ok": True, "error_code": ""}
    sent_button = channel.send.await_args.kwargs["view"].children[0]
    updated_button = existing_message.edit.await_args.kwargs["view"].children[0]
    assert decode_custom_id(sent_button.custom_id)["plugin_id"] == "canonical-plugin"
    assert decode_custom_id(updated_button.custom_id)["plugin_id"] == "canonical-plugin"
