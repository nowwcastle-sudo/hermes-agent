"""Discord rendering and REST I/O for plugin-owned interactions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from hermes_cli.discord_interactions import DiscordInteractions, decode_custom_id
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
    assert button.value == "choice-a"
    assert button.callback is None
    assert decode_custom_id(button.custom_id) == {
        "plugin_id": "cs-quiz",
        "action": "submit_choice",
        "route_token": "A" * 24,
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
