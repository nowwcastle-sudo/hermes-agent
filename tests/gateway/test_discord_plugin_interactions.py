"""Discord rendering and REST I/O for plugin-owned interactions."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import re
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from hermes_cli.discord_interactions import (
    DiscordInteractions,
    decode_custom_id,
    encode_custom_id,
)
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from plugins.platforms.discord.adapter import DiscordAdapter
import plugins.platforms.discord.adapter as discord_adapter_module
from plugins.platforms.discord.plugin_interactions import (
    DiscordPluginInteractionBridge,
)
import plugins.platforms.discord.plugin_interactions as plugin_interactions_module


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
        edit_message=AsyncMock(),
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


def _plugin_context(manager: PluginManager) -> PluginContext:
    return PluginContext(
        PluginManifest(name="display-name", key="plugin-one", source="user"),
        manager,
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


class _ListenerBot:
    def __init__(self, **_kwargs):
        self.user = SimpleNamespace(id=999, name="Hermes")
        self.guilds = []
        self.cached_messages = []
        self.persistent_views = []
        self._closed = False
        self.events = {}
        self.listeners = defaultdict(list)

    def event(self, callback):
        self.events[callback.__name__] = callback
        return callback

    def add_listener(self, callback, name):
        self.listeners[name].append(callback)

    async def start(self, _token):
        await self.events["on_ready"]()

    async def close(self):
        self._closed = True

    def is_closed(self):
        return self._closed


def _prepare_listener_connect(monkeypatch, adapter):
    created = []

    def make_bot(**kwargs):
        bot = _ListenerBot(**kwargs)
        created.append(bot)
        return bot

    intents = SimpleNamespace(
        message_content=False,
        dm_messages=False,
        guild_messages=False,
        members=False,
        voice_states=False,
    )
    monkeypatch.setattr(
        "gateway.status.acquire_scoped_lock", lambda *_args, **_kwargs: (True, None)
    )
    monkeypatch.setattr("gateway.status.release_scoped_lock", lambda *_args: None)
    monkeypatch.setattr(discord_adapter_module.Intents, "default", lambda: intents)
    monkeypatch.setattr(discord_adapter_module.commands, "Bot", make_bot)
    monkeypatch.setattr(adapter, "_resolve_allowed_usernames", AsyncMock())
    monkeypatch.setattr(adapter, "_run_post_connect_initialization", AsyncMock())
    monkeypatch.setattr(adapter, "_start_liveness_probe", MagicMock())
    monkeypatch.setattr(adapter, "_handle_bot_task_done", MagicMock())
    return created


@pytest.mark.asyncio
async def test_connect_registers_exactly_one_interaction_listener_with_add_listener(
    monkeypatch,
) -> None:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)

    assert await adapter.connect() is True

    assert len(created) == 1
    assert list(created[0].listeners) == ["on_interaction"]
    assert len(created[0].listeners["on_interaction"]) == 1


@pytest.mark.asyncio
async def test_non_owned_interaction_coexists_with_existing_view_callback(
    monkeypatch,
) -> None:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)
    adapter._plugin_interactions.handle_interaction = AsyncMock()
    existing_view_callback = AsyncMock()

    assert await adapter.connect() is True
    interaction = _fake_interaction("clarify.choice")
    listener = created[0].listeners["on_interaction"][0]

    await listener(interaction)
    await existing_view_callback(interaction)

    adapter._plugin_interactions.handle_interaction.assert_not_awaited()
    existing_view_callback.assert_awaited_once_with(interaction)
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["button", "modal_submit"])
async def test_owned_button_and_modal_reach_bridge_exactly_once(
    monkeypatch,
    kind,
) -> None:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)
    adapter._plugin_interactions.handle_interaction = AsyncMock(return_value=True)

    assert await adapter.connect() is True
    custom_id = encode_custom_id("plugin-one", "submit", "A" * 16)
    interaction = (
        _fake_interaction(custom_id)
        if kind == "button"
        else _fake_modal_submit(custom_id, [])
    )

    await created[0].listeners["on_interaction"][0](interaction)

    adapter._plugin_interactions.handle_interaction.assert_awaited_once_with(interaction)


@pytest.mark.asyncio
async def test_reconnect_registers_once_on_each_new_client_without_double_dispatch(
    monkeypatch,
) -> None:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)
    adapter._plugin_interactions.handle_interaction = AsyncMock(return_value=True)

    assert await adapter.connect() is True
    first_interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit", "A" * 16)
    )
    await created[0].listeners["on_interaction"][0](first_interaction)
    assert await adapter.connect(is_reconnect=True) is True

    assert len(created) == 2
    assert created[0].is_closed() is True
    assert [len(bot.listeners["on_interaction"]) for bot in created] == [1, 1]
    second_interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit", "A" * 16)
    )

    await created[1].listeners["on_interaction"][0](second_interaction)

    assert adapter._plugin_interactions.handle_interaction.await_args_list == [
        ((first_interaction,), {}),
        ((second_interaction,), {}),
    ]


@pytest.mark.asyncio
async def test_full_unload_reload_routes_listener_once_to_one_fresh_handler(
    monkeypatch,
) -> None:
    manager = PluginManager()
    handler_a = AsyncMock(return_value={"kind": "no_change"})
    handler_b = AsyncMock(return_value={"kind": "no_change"})
    monkeypatch.setattr("hermes_cli.plugins.plugin_capability_granted", lambda *_: True)
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugin_interactions_module, "plugin_capability_granted", lambda *_: True
    )
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", lambda *_: True)
    registration_a = _plugin_context(manager).register_discord_interaction(handler_a)
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)

    assert await adapter.connect() is True
    assert manager.unload() is True
    assert registration_a.active is False
    assert manager.get_discord_interaction_handler("plugin-one") is None
    assert manager._discord_interaction_handlers == {}

    registration_b = _plugin_context(manager).register_discord_interaction(handler_b)
    assert manager._discord_interaction_handlers == {"plugin-one": handler_b}
    assert len(created[0].listeners["on_interaction"]) == 1
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )

    await created[0].listeners["on_interaction"][0](interaction)

    assert registration_b.active is True
    handler_a.assert_not_awaited()
    handler_b.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_stable_custom_id_routes_with_fresh_bridge_client_and_empty_cache(
    monkeypatch,
) -> None:
    manager = PluginManager()
    handler = AsyncMock(return_value={"kind": "no_change"})
    monkeypatch.setattr("hermes_cli.plugins.plugin_capability_granted", lambda *_: True)
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugin_interactions_module, "plugin_capability_granted", lambda *_: True
    )
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", lambda *_: True)
    registration = _plugin_context(manager).register_discord_interaction(handler)
    original_bridge = DiscordPluginInteractionBridge(adapter=None)
    stable_custom_id = original_bridge.build_message_kwargs(
        "plugin-one", _message_spec()
    )["view"].children[0].custom_id

    fresh_adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, fresh_adapter)

    assert await fresh_adapter.connect() is True
    assert fresh_adapter._plugin_interactions is not original_bridge
    assert len(created) == 1
    assert created[0].cached_messages == []
    assert created[0].persistent_views == []
    interaction = _fake_interaction(stable_custom_id)

    await created[0].listeners["on_interaction"][0](interaction)

    assert registration.active is True
    assert manager.get_discord_interaction_handler("plugin-one") is handler
    handler.assert_awaited_once()
    payload = handler.await_args.args[0]
    assert payload["plugin_id"] == "plugin-one"
    assert payload["route_token"] == "A" * 24
    assert payload["component_value"] == "choice-a"
    interaction.response.defer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_listener_contains_bridge_exception_with_one_trace_and_no_private_data(
    monkeypatch,
    caplog,
) -> None:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)
    private_markers = [
        "PRIVATE_COMPONENT_VALUE",
        "PRIVATE_ROUTE_TOKEN",
        "PRIVATE_USER_DATA",
        "PRIVATE_CREDENTIAL",
    ]
    adapter._plugin_interactions.handle_interaction = AsyncMock(
        side_effect=RuntimeError(" ".join(private_markers))
    )
    assert await adapter.connect() is True
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit", "A" * 16, private_markers[0])
    )
    caplog.clear()

    await created[0].listeners["on_interaction"][0](interaction)

    adapter._plugin_interactions.handle_interaction.assert_awaited_once_with(interaction)
    assert len(caplog.messages) == 1
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", caplog.messages[0])
    assert len(trace_ids) == 1
    assert caplog.messages == [
        f"discord_plugin_interaction_listener_failed trace={trace_ids[0]}"
    ]
    for marker in private_markers:
        assert marker not in caplog.text


@pytest.mark.asyncio
async def test_listener_contains_namespace_inspection_exception_with_one_trace(
    monkeypatch,
    caplog,
) -> None:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener_connect(monkeypatch, adapter)
    adapter._plugin_interactions.handle_interaction = AsyncMock()

    class RaisingInteraction:
        @property
        def data(self):
            raise RuntimeError("PRIVATE_NAMESPACE_DATA")

    assert await adapter.connect() is True
    caplog.clear()

    await created[0].listeners["on_interaction"][0](RaisingInteraction())

    adapter._plugin_interactions.handle_interaction.assert_not_awaited()
    assert len(caplog.messages) == 1
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", caplog.messages[0])
    assert len(trace_ids) == 1
    assert caplog.messages == [
        f"discord_plugin_interaction_listener_failed trace={trace_ids[0]}"
    ]
    assert "PRIVATE_NAMESPACE_DATA" not in caplog.text


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
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_canonical_plugin_id_uses_missing_handler_fail_closed_path(
    monkeypatch,
) -> None:
    known_handler = AsyncMock(return_value={"kind": "no_change"})
    manager = SimpleNamespace(
        get_discord_interaction_handler=MagicMock(
            side_effect=lambda plugin_id: (
                known_handler if plugin_id == "plugin-one" else None
            )
        )
    )
    capability_granted = MagicMock(return_value=True)
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugin_interactions_module, "plugin_capability_granted", capability_granted
    )
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", lambda *_: True)
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("unknown-plugin", "submit_choice", "A" * 16)
    )

    assert await bridge.handle_interaction(interaction) is True

    manager.get_discord_interaction_handler.assert_called_once_with("unknown-plugin")
    capability_granted.assert_not_called()
    known_handler.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(
        "현재 사용할 수 없는 기능이야.", ephemeral=True
    )
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "custom_id",
    [
        "hdi1.Y3MtcXVpeg==.submit_choice." + "A" * 16,
        "hdi1.Y3MtcXVpeg.SubmitChoice." + "A" * 16,
        "hdi1.Y3MtcXVpeg.submit_choice.short",
        "hdi1.Y3MtcXVpeg.submit_choice." + "A" * 16 + ".YQ==",
    ],
    ids=[
        "noncanonical-plugin-segment",
        "noncanonical-action",
        "malformed-route-token",
        "noncanonical-component-value",
    ],
)
async def test_malformed_or_noncanonical_custom_id_fails_before_routing(
    monkeypatch,
    custom_id: str,
) -> None:
    auth = MagicMock(return_value=True)
    get_manager = MagicMock()
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", auth)
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", get_manager)
    bridge = DiscordPluginInteractionBridge(adapter=SimpleNamespace())
    interaction = _fake_interaction(custom_id)

    assert await bridge.handle_interaction(interaction) is True

    auth.assert_not_called()
    get_manager.assert_not_called()
    interaction.response.send_message.assert_awaited_once_with(
        "유효하지 않은 요청이야.", ephemeral=True
    )
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


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
@pytest.mark.parametrize(
    "second_decision",
    [False, OSError("PRIVATE_CONSENT_BACKEND")],
    ids=["runtime-revoke", "consent-read-exception"],
)
async def test_existing_bridge_and_registration_fail_closed_after_runtime_revoke(
    monkeypatch,
    second_decision: object,
) -> None:
    manager = PluginManager()
    handler = AsyncMock(return_value={"kind": "no_change"})
    capability = {"decision": True}

    def capability_granted(*_args) -> bool:
        decision = capability["decision"]
        if isinstance(decision, BaseException):
            raise decision
        return bool(decision)

    monkeypatch.setattr(
        "hermes_cli.plugins.plugin_capability_granted", capability_granted
    )
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        capability_granted,
    )
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugin_interactions_module, "plugin_capability_granted", capability_granted
    )
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", lambda *_: True)
    ctx = _plugin_context(manager)
    registration = ctx.register_discord_interaction(handler)
    facade = ctx.discord
    adapter = SimpleNamespace(
        _allowed_user_ids={"202"},
        _allowed_role_ids=set(),
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
        "gateway.run._gateway_runner_ref",
        lambda: SimpleNamespace(adapters={Platform.DISCORD: adapter}),
    )
    bridge = DiscordPluginInteractionBridge(adapter=adapter)
    custom_id = encode_custom_id("plugin-one", "open_form", "A" * 16)
    granted_interaction = _fake_interaction(custom_id)
    rejected_interaction = _fake_interaction(custom_id)

    assert await bridge.handle_interaction(granted_interaction) is True
    assert await facade.send("10", _message_spec()) == {
        "ok": True,
        "channel_id": "10",
        "message_id": "20",
        "error_code": "",
    }

    capability["decision"] = second_decision
    assert await bridge.handle_interaction(rejected_interaction) is True
    send_result = await facade.send("10", _message_spec())
    update_result = await facade.update("10", "20", _message_spec())

    assert registration.active is True
    assert ctx.discord is facade
    assert manager.get_discord_interaction_handler("plugin-one") is handler
    assert send_result == {"ok": False, "error_code": "capability_not_granted"}
    assert update_result == {"ok": False, "error_code": "capability_not_granted"}
    handler.assert_awaited_once()
    adapter.plugin_interaction_send.assert_awaited_once()
    adapter.plugin_interaction_update.assert_not_awaited()
    granted_interaction.response.send_message.assert_awaited_once()
    rejected_interaction.response.send_message.assert_awaited_once()
    assert rejected_interaction.response.send_message.await_args.kwargs == {
        "ephemeral": True
    }
    rejected_interaction.response.defer.assert_not_awaited()
    rejected_interaction.response.send_modal.assert_not_awaited()
    rejected_interaction.edit_original_response.assert_not_awaited()
    rejected_interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("release_kind", ["dispose", "targeted_unload"])
async def test_released_handler_identity_is_not_resurrected_after_reregistration(
    monkeypatch,
    release_kind: str,
) -> None:
    manager = PluginManager()
    handler_a = AsyncMock(return_value={"kind": "no_change"})
    handler_b = AsyncMock(return_value={"kind": "no_change"})
    monkeypatch.setattr("hermes_cli.plugins.plugin_capability_granted", lambda *_: True)
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugin_interactions_module, "plugin_capability_granted", lambda *_: True
    )
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", lambda *_: True)
    registration_a = _plugin_context(manager).register_discord_interaction(handler_a)

    if release_kind == "dispose":
        registration_a.dispose()
    else:
        assert manager.unload("plugin-one") is True

    assert registration_a.active is False
    assert manager.get_discord_interaction_handler("plugin-one") is None

    registration_b = _plugin_context(manager).register_discord_interaction(handler_b)
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )

    assert await bridge.handle_interaction(interaction) is True

    assert registration_b.active is True
    assert manager.get_discord_interaction_handler("plugin-one") is handler_b
    handler_a.assert_not_awaited()
    handler_b.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutated_field", "mutated_value"),
    [
        ("route_token", "B" * 16),
        ("channel_id", "999"),
        ("message_id", "888"),
    ],
)
async def test_generic_fixture_rejects_each_stale_route_channel_or_message(
    monkeypatch,
    mutated_field: str,
    mutated_value: str,
) -> None:
    expected = {
        "route_token": "A" * 16,
        "channel_id": "404",
        "message_id": "505",
    }
    calls = []

    async def handler(payload):
        calls.append(payload)
        actual = {key: payload[key] for key in expected}
        if actual != expected:
            return {"kind": "ephemeral", "content": "stale interaction"}
        return {"kind": "no_change"}

    custom_id = encode_custom_id(
        "plugin-one",
        "submit_choice",
        mutated_value if mutated_field == "route_token" else expected["route_token"],
    )
    interaction = _fake_interaction(custom_id)
    if mutated_field in {"channel_id", "message_id"}:
        if mutated_field == "channel_id":
            interaction.channel_id = int(mutated_value)
        else:
            interaction.message.id = int(mutated_value)
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    assert len(calls) == 1
    actual = {key: calls[0][key] for key in expected}
    assert actual == {**expected, mutated_field: mutated_value}
    interaction.response.defer.assert_awaited_once_with()
    interaction.followup.send.assert_awaited_once_with(
        "stale interaction", ephemeral=True
    )
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_component_value_mutation_preserves_route_and_changes_exact_payload(
    monkeypatch,
) -> None:
    payloads = []

    async def handler(payload):
        payloads.append(payload)
        if payload.get("component_value") != "choice-a":
            return {"kind": "ephemeral", "content": "stale component"}
        return {"kind": "no_change"}

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    _allow_handler(monkeypatch, handler)
    accepted = _fake_interaction(
        encode_custom_id(
            "plugin-one", "submit_choice", "A" * 16, component_value="choice-a"
        )
    )
    mutated = _fake_interaction(
        encode_custom_id(
            "plugin-one", "submit_choice", "A" * 16, component_value="choice-b"
        )
    )

    assert await bridge.handle_interaction(accepted) is True
    assert await bridge.handle_interaction(mutated) is True

    assert [
        (
            payload["plugin_id"],
            payload["action"],
            payload["route_token"],
            payload["component_value"],
            payload["user_id"],
            payload["channel_id"],
            payload["message_id"],
        )
        for payload in payloads
    ] == [
        (
            "plugin-one",
            "submit_choice",
            "A" * 16,
            "choice-a",
            "202",
            "404",
            "505",
        ),
        (
            "plugin-one",
            "submit_choice",
            "A" * 16,
            "choice-b",
            "202",
            "404",
            "505",
        ),
    ]
    accepted.response.defer.assert_awaited_once_with()
    accepted.followup.send.assert_not_awaited()
    mutated.response.defer.assert_awaited_once_with()
    mutated.followup.send.assert_awaited_once_with("stale component", ephemeral=True)
    for interaction in (accepted, mutated):
        interaction.response.send_message.assert_not_awaited()
        interaction.response.send_modal.assert_not_awaited()
        interaction.edit_original_response.assert_not_awaited()


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
async def test_open_prefix_update_message_uses_one_initial_edit_ack(
    monkeypatch,
) -> None:
    handler = AsyncMock(
        return_value={"kind": "update_message", "message": _message_spec()}
    )
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_dashboard", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_not_awaited()
    handler.assert_awaited_once()
    interaction.response.edit_message.assert_awaited_once()
    assert interaction.response.edit_message.await_args.kwargs["content"] == "Choose one"
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_prefix_oversized_update_is_rejected_before_edit_with_one_ack(
    monkeypatch,
) -> None:
    spec = _message_spec()
    spec["content"] = "x" * 2_001
    handler = AsyncMock(return_value={"kind": "update_message", "message": spec})
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_dashboard", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_not_awaited()
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs == {"ephemeral": True}
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_undeferred_initial_edit_transport_failure_does_not_retry_ack(
    monkeypatch,
    caplog,
) -> None:
    handler = AsyncMock(
        return_value={"kind": "update_message", "message": _message_spec()}
    )
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_dashboard", "A" * 16)
    )
    interaction.response.edit_message.side_effect = RuntimeError("PRIVATE_EDIT_FAILURE")
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    assert "operation=result_apply" in caplog.text
    assert "PRIVATE_EDIT_FAILURE" not in caplog.text


@pytest.mark.asyncio
async def test_undeferred_prepare_failure_sends_safe_initial_ack(
    monkeypatch,
    caplog,
) -> None:
    handler = AsyncMock(return_value=_modal_result())
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )
    monkeypatch.setattr(
        bridge,
        "_build_modal",
        MagicMock(side_effect=RuntimeError("PRIVATE_PREPARE_FAILURE")),
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.send_message.assert_awaited_once()
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.edit_message.assert_not_awaited()
    assert "operation=result_prepare" in caplog.text
    assert "PRIVATE_PREPARE_FAILURE" not in caplog.text


@pytest.mark.asyncio
async def test_safe_error_delivery_failure_is_isolated(monkeypatch, caplog) -> None:
    handler = AsyncMock(side_effect=RuntimeError("PRIVATE_HANDLER_FAILURE"))
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )
    interaction.response.send_message.side_effect = RuntimeError(
        "PRIVATE_ERROR_ACK_FAILURE"
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.send_message.assert_awaited_once()
    assert "operation=handler_exception" in caplog.text
    assert "operation=error_response" in caplog.text
    assert "PRIVATE_HANDLER_FAILURE" not in caplog.text
    assert "PRIVATE_ERROR_ACK_FAILURE" not in caplog.text


@pytest.mark.parametrize(
    ("result", "response_method"),
    [
        (_modal_result(), "send_modal"),
        ({"kind": "ephemeral", "content": "Visible only to you"}, "send_message"),
        ({"kind": "no_change"}, "send_message"),
    ],
)
@pytest.mark.asyncio
async def test_undeferred_transport_failure_is_isolated_without_retry(
    monkeypatch,
    caplog,
    result,
    response_method,
) -> None:
    handler = AsyncMock(return_value=result)
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )
    getattr(interaction.response, response_method).side_effect = RuntimeError(
        "PRIVATE_TRANSPORT_FAILURE"
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    initial_response_count = (
        interaction.response.send_modal.await_count
        + interaction.response.edit_message.await_count
        + interaction.response.send_message.await_count
    )
    assert initial_response_count == 1
    interaction.response.defer.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    assert "operation=result_apply" in caplog.text
    assert "PRIVATE_TRANSPORT_FAILURE" not in caplog.text


@pytest.mark.asyncio
async def test_undeferred_initial_edit_local_failure_after_ack_does_not_ack_again(
    monkeypatch,
    caplog,
) -> None:
    handler = AsyncMock(
        return_value={"kind": "update_message", "message": _message_spec()}
    )
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_dashboard", "A" * 16)
    )
    response_done = False

    def is_done() -> bool:
        return response_done

    async def accepted_then_failed(**_kwargs) -> None:
        nonlocal response_done
        response_done = True
        raise RuntimeError("PRIVATE_LOCAL_FAILURE")

    interaction.response.is_done.side_effect = is_done
    interaction.response.edit_message.side_effect = accepted_then_failed
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    assert "operation=result_apply" in caplog.text
    assert "PRIVATE_LOCAL_FAILURE" not in caplog.text


@pytest.mark.asyncio
async def test_undeferred_unknown_interaction_edit_failure_does_not_retry_ack(
    monkeypatch,
) -> None:
    class UnknownInteraction(Exception):
        code = 10062

    handler = AsyncMock(
        return_value={"kind": "update_message", "message": _message_spec()}
    )
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_dashboard", "A" * 16)
    )
    interaction.response.edit_message.side_effect = UnknownInteraction("expired")
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_undeferred_ephemeral_sends_one_initial_ephemeral_response(
    monkeypatch,
) -> None:
    handler = AsyncMock(
        return_value={"kind": "ephemeral", "content": "Visible only to you"}
    )
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    handler.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(
        "Visible only to you", ephemeral=True
    )
    interaction.response.send_modal.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_undeferred_no_change_sends_one_bounded_initial_ephemeral_ack(
    monkeypatch,
) -> None:
    handler = AsyncMock(return_value={"kind": "no_change"})
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "open_form", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    handler.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    content = interaction.response.send_message.await_args.args[0]
    assert 1 <= len(content) <= 200
    assert interaction.response.send_message.await_args.kwargs == {"ephemeral": True}
    interaction.response.send_modal.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_button_component_value_is_preserved_in_modal_custom_id(
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
        encode_custom_id("canonical-plugin", "open_form", "R" * 24, "q7")
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    payload = handler.await_args.args[0]
    assert payload["component_value"] == "q7"
    modal = interaction.response.send_modal.await_args.args[0]
    assert decode_custom_id(modal.custom_id) == {
        "plugin_id": "canonical-plugin",
        "action": "submit_inherited",
        "route_token": "R" * 24,
        "component_value": "q7",
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
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_ephemeral_sends_one_followup_without_second_initial_response(
    monkeypatch,
) -> None:
    handler = AsyncMock(
        return_value={"kind": "ephemeral", "content": "Deferred private reply"}
    )
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_awaited_once_with()
    handler.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with(
        "Deferred private reply", ephemeral=True
    )
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_no_change_uses_defer_as_sole_ack(monkeypatch) -> None:
    handler = AsyncMock(return_value={"kind": "no_change"})
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_awaited_once_with()
    handler.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_handler_runs_in_thread_and_is_invoked_once(monkeypatch) -> None:
    calls = []
    to_thread_calls = []
    real_to_thread = asyncio.to_thread

    def handler(payload):
        calls.append(payload)
        return {"kind": "no_change"}

    async def recording_to_thread(func, *args):
        to_thread_calls.append((func, args))
        return await real_to_thread(func, *args)

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)
    monkeypatch.setattr(plugin_interactions_module.asyncio, "to_thread", recording_to_thread)

    assert await bridge.handle_interaction(interaction) is True

    assert len(calls) == 1
    assert calls[0]["action"] == "submit_choice"
    assert to_thread_calls == [(handler, (calls[0],))]
    interaction.response.defer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_coroutine_handler_is_awaited_directly_and_invoked_once(monkeypatch) -> None:
    calls = []

    async def handler(payload):
        calls.append(payload)
        return {"kind": "no_change"}

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)
    to_thread = AsyncMock()
    monkeypatch.setattr(plugin_interactions_module.asyncio, "to_thread", to_thread)

    assert await bridge.handle_interaction(interaction) is True

    assert len(calls) == 1
    assert calls[0]["action"] == "submit_choice"
    to_thread.assert_not_awaited()
    interaction.response.defer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_sync_handler_returned_awaitable_is_awaited_without_reinvocation(
    monkeypatch,
) -> None:
    handler_calls = []
    await_calls = []

    async def finish():
        await_calls.append("awaited")
        return {"kind": "no_change"}

    def handler(payload):
        handler_calls.append(payload)
        return finish()

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    assert len(handler_calls) == 1
    assert await_calls == ["awaited"]
    interaction.response.defer.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_timeout", "result"),
    [
        ("open_form", 0.011, _modal_result()),
        ("submit_choice", 0.022, {"kind": "no_change"}),
    ],
)
async def test_handler_wait_for_selects_open_and_deferred_timeout_constants(
    monkeypatch,
    action: str,
    expected_timeout: float,
    result: dict,
) -> None:
    invoked = asyncio.Event()
    observed_timeouts = []
    real_wait_for = asyncio.wait_for

    async def handler(_payload):
        invoked.set()
        return result

    async def recording_wait_for(awaitable, *, timeout):
        observed_timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout=timeout)

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", action, "A" * 16)
    )
    _allow_handler(monkeypatch, handler)
    monkeypatch.setattr(
        plugin_interactions_module, "_OPEN_HANDLER_TIMEOUT_SECONDS", 0.011
    )
    monkeypatch.setattr(
        plugin_interactions_module, "_DEFERRED_HANDLER_TIMEOUT_SECONDS", 0.022
    )
    monkeypatch.setattr(plugin_interactions_module.asyncio, "wait_for", recording_wait_for)

    assert await bridge.handle_interaction(interaction) is True

    assert invoked.is_set()
    assert observed_timeouts == [expected_timeout]


@pytest.mark.asyncio
async def test_blocking_sync_open_handler_times_out_after_start_with_one_ack(
    monkeypatch,
    caplog,
) -> None:
    release = threading.Event()
    events = []
    handler_calls = []

    def handler(payload):
        handler_calls.append(payload)
        events.append("handler_started")
        release.wait()
        return _modal_result()

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id(
            "plugin-one", "open_form", "R" * 24, "PRIVATE_COMPONENT_VALUE"
        )
    )
    interaction.response.send_message.side_effect = (
        lambda *_args, **_kwargs: events.append("timeout_ack")
    )
    _allow_handler(monkeypatch, handler)
    monkeypatch.setattr(
        plugin_interactions_module, "_OPEN_HANDLER_TIMEOUT_SECONDS", 0.01
    )

    try:
        assert await bridge.handle_interaction(interaction) is True
    finally:
        release.set()

    assert events == ["handler_started", "timeout_ack"]
    assert len(handler_calls) == 1
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs == {"ephemeral": True}
    user_error = interaction.response.send_message.await_args.args[0]
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", user_error)
    assert len(trace_ids) == 1
    assert caplog.messages == [
        "discord_plugin_interaction_failed "
        f"operation=handler_timeout plugin=plugin-one trace={trace_ids[0]}"
    ]
    exposed = f"{user_error}\n{caplog.text}"
    assert "PRIVATE_COMPONENT_VALUE" not in exposed
    assert "R" * 24 not in exposed
    interaction.response.send_modal.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_async_deferred_handler_times_out_after_defer_with_one_error(
    monkeypatch,
) -> None:
    pending = asyncio.get_running_loop().create_future()
    events = []
    handler_calls = []

    async def handler(payload):
        handler_calls.append(payload)
        events.append("handler_started")
        return await pending

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    interaction.response.defer.side_effect = lambda: events.append("defer")
    interaction.followup.send.side_effect = (
        lambda *_args, **_kwargs: events.append("timeout_error")
    )
    _allow_handler(monkeypatch, handler)
    monkeypatch.setattr(
        plugin_interactions_module, "_DEFERRED_HANDLER_TIMEOUT_SECONDS", 0.01
    )

    assert await bridge.handle_interaction(interaction) is True

    assert events == ["defer", "handler_started", "timeout_error"]
    assert len(handler_calls) == 1
    assert pending.cancelled()
    interaction.response.defer.assert_awaited_once_with()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs == {"ephemeral": True}
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_handler_exception_uses_trace_safe_error_without_private_data(
    monkeypatch,
    caplog,
) -> None:
    private_exception_markers = (
        "PRIVATE_EXCEPTION",
        "PRIVATE_PROMPT",
        "PRIVATE_TOKEN",
        "PRIVATE_CREDENTIAL",
        "PRIVATE_MESSAGE_SPEC",
    )
    private_exception = " ".join(private_exception_markers)
    handler_calls = []

    async def handler(payload):
        handler_calls.append(payload)
        raise RuntimeError(private_exception)

    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id(
            "plugin-one", "submit_choice", "R" * 24, "PRIVATE_COMPONENT_VALUE"
        )
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    assert len(handler_calls) == 1
    interaction.response.defer.assert_awaited_once_with()
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs == {"ephemeral": True}
    user_error = interaction.followup.send.await_args.args[0]
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", user_error)
    assert len(trace_ids) == 1
    assert caplog.messages == [
        "discord_plugin_interaction_failed "
        f"operation=handler_exception plugin=plugin-one trace={trace_ids[0]}"
    ]
    for private in (
        *private_exception_markers,
        "PRIVATE_COMPONENT_VALUE",
        "R" * 24,
    ):
        assert private not in user_error
        assert private not in caplog.text
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"kind": "PRIVATE_RESULT_KIND"},
        {
            "kind": "ephemeral",
            "content": "PRIVATE_RESULT_CONTENT",
            "extra": "PRIVATE_RESULT_SCHEMA",
        },
        {"kind": "ephemeral", "content": {"PRIVATE_NON_JSON"}},
    ],
    ids=["invalid-kind", "invalid-schema", "non-json"],
)
async def test_invalid_handler_result_is_normalized_to_trace_safe_error(
    monkeypatch,
    caplog,
    result,
) -> None:
    handler = AsyncMock(return_value=result)
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "R" * 24)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    handler.assert_awaited_once()
    interaction.response.defer.assert_awaited_once_with()
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs == {"ephemeral": True}
    user_error = interaction.followup.send.await_args.args[0]
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", user_error)
    assert len(trace_ids) == 1
    assert caplog.messages == [
        "discord_plugin_interaction_failed "
        f"operation=result_validation plugin=plugin-one trace={trace_ids[0]}"
    ]
    exposed = f"{user_error}\n{caplog.text}"
    for private in (
        "PRIVATE_RESULT_KIND",
        "PRIVATE_RESULT_CONTENT",
        "PRIVATE_RESULT_SCHEMA",
        "PRIVATE_NON_JSON",
        "R" * 24,
    ):
        assert private not in exposed
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_submit_defers_before_handler_with_nested_text_values(
    monkeypatch,
) -> None:
    events = []

    async def handler(payload):
        events.append(("handler", payload["kind"]))
        assert "component_value" not in payload
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
        encode_custom_id("plugin-one", "submit_answer", "B" * 16, "q7"),
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
            "component_value": "q7",
            "modal_values": {"answer": "two"},
        },
    ]
    assert all(json.dumps(payload) for payload in payloads)


@pytest.mark.asyncio
async def test_deferred_modal_submit_rejects_open_modal_without_second_ack(
    monkeypatch,
    caplog,
) -> None:
    result = _modal_result()
    result["modal"]["title"] = "PRIVATE_MODAL_TITLE"
    handler = AsyncMock(return_value=result)
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
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", args[0])
    assert len(trace_ids) == 1
    assert caplog.messages == [
        "discord_plugin_interaction_failed "
        f"operation=result_validation plugin=plugin-one trace={trace_ids[0]}"
    ]
    assert "PRIVATE_MODAL_TITLE" not in args[0]
    assert "PRIVATE_MODAL_TITLE" not in caplog.text


@pytest.mark.asyncio
async def test_deferred_button_rejects_open_modal_with_post_defer_safe_error(
    monkeypatch,
    caplog,
) -> None:
    handler = AsyncMock(return_value=_modal_result())
    bridge = DiscordPluginInteractionBridge(
        adapter=SimpleNamespace(_allowed_user_ids={"202"}, _allowed_role_ids=set())
    )
    interaction = _fake_interaction(
        encode_custom_id("plugin-one", "submit_choice", "A" * 16)
    )
    _allow_handler(monkeypatch, handler)

    assert await bridge.handle_interaction(interaction) is True

    interaction.response.defer.assert_awaited_once_with()
    handler.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    user_error = interaction.followup.send.await_args.args[0]
    assert interaction.followup.send.await_args.kwargs == {"ephemeral": True}
    trace_ids = re.findall(r"\b[0-9a-f]{32}\b", user_error)
    assert len(trace_ids) == 1
    assert caplog.messages == [
        "discord_plugin_interaction_failed "
        f"operation=result_validation plugin=plugin-one trace={trace_ids[0]}"
    ]


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
