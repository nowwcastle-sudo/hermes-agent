"""Native discord.py serialization smoke tests for plugin components."""

from __future__ import annotations

import discord

from hermes_cli.discord_interactions import decode_custom_id
from plugins.platforms.discord.plugin_interactions import (
    DiscordPluginInteractionBridge,
)


def test_real_discord_button_serialization_carries_component_value_in_custom_id() -> None:
    spec = {
        "api_version": 1,
        "content": "Choose one",
        "embeds": [],
        "components": [
            {
                "type": "button",
                "action": "submit_choice",
                "route_token": "A" * 24,
                "label": "Choice A",
                "style": "success",
                "value": "choice-a",
                "disabled": False,
            }
        ],
    }

    view = DiscordPluginInteractionBridge(adapter=None).build_message_kwargs(
        "cs-quiz", spec
    )["view"]
    button = view.children[0]
    serialized_button = button.to_component_dict()
    serialized_view = view.to_components()

    assert isinstance(button, discord.ui.Button)
    assert serialized_view[0]["components"] == [serialized_button]
    assert serialized_button["custom_id"] == button.custom_id
    assert decode_custom_id(serialized_button["custom_id"])["component_value"] == (
        "choice-a"
    )