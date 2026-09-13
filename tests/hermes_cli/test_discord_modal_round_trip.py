"""Native discord.py open-button to modal-submit flow coverage."""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from gateway.config import PlatformConfig
from hermes_cli.discord_interactions import encode_custom_id
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from plugins.platforms.discord.adapter import DiscordAdapter
import plugins.platforms.discord.adapter as discord_adapter_module
import plugins.platforms.discord.plugin_interactions as plugin_interactions_module


class _ListenerBot:
    def __init__(self, **_kwargs):
        self.user = SimpleNamespace(id=999, name="Hermes")
        self.guilds = []
        self.cached_messages = []
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


def _interaction(
    custom_id: str,
    *,
    interaction_type: discord.InteractionType = discord.InteractionType.component,
    components: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        type=interaction_type,
        data={"custom_id": custom_id, **({"components": components} if components else {})},
        user=SimpleNamespace(id=202, roles=[]),
        guild_id=303,
        channel_id=404,
        message=SimpleNamespace(id=505),
        response=SimpleNamespace(
            is_done=MagicMock(return_value=False),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def _prepare_listener(monkeypatch, adapter: DiscordAdapter) -> list[_ListenerBot]:
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
async def test_native_modal_round_trip_preserves_open_button_component_value(
    monkeypatch,
) -> None:
    manager = PluginManager()
    payloads = []

    async def handler(payload):
        payloads.append(payload)
        if payload["kind"] == "button":
            return {
                "kind": "open_modal",
                "modal": {
                    "api_version": 1,
                    "action": "submit_answer",
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
        return {"kind": "no_change"}

    monkeypatch.setattr("hermes_cli.plugins.plugin_capability_granted", lambda *_: True)
    monkeypatch.setattr(plugin_interactions_module, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugin_interactions_module, "plugin_capability_granted", lambda *_: True
    )
    monkeypatch.setattr(plugin_interactions_module, "_component_check_auth", lambda *_: True)
    context = PluginContext(
        PluginManifest(name="display-name", key="plugin-one", source="user"), manager
    )
    registration = context.register_discord_interaction(handler)
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="fixture-token",
            extra={"slash_commands": False},
        )
    )
    created = _prepare_listener(monkeypatch, adapter)

    assert await adapter.connect() is True
    listener = created[0].listeners["on_interaction"][0]
    opening = _interaction(
        encode_custom_id("plugin-one", "open_short_answer", "R" * 24, "q7")
    )
    await listener(opening)

    modal = opening.response.send_modal.await_args.args[0]
    assert registration.active is True
    assert isinstance(modal, discord.ui.Modal)
    assert isinstance(modal.children[0], discord.ui.TextInput)

    submission = _interaction(
        modal.custom_id,
        interaction_type=discord.InteractionType.modal_submit,
        components=[
            {
                "type": 1,
                "components": [
                    {"type": 4, "custom_id": "answer", "value": "two"}
                ],
            }
        ],
    )
    await listener(submission)

    assert len(payloads) == 2
    opening_route = {
        key: payloads[0][key]
        for key in ("plugin_id", "action", "route_token", "component_value")
    }
    submitted_route = {
        key: payloads[1][key]
        for key in ("plugin_id", "action", "route_token", "component_value")
    }
    assert opening_route == {
        "plugin_id": "plugin-one",
        "action": "open_short_answer",
        "route_token": "R" * 24,
        "component_value": "q7",
    }
    assert submitted_route == {**opening_route, "action": "submit_answer"}
    assert payloads[1]["kind"] == "modal_submit"
    assert payloads[1]["modal_values"] == {"answer": "two"}
    submission.response.defer.assert_awaited_once_with()
