"""Render validated plugin interaction specs with native Discord objects."""

from __future__ import annotations

from typing import Any

import discord

from hermes_cli.discord_interactions import encode_custom_id, validate_message_spec

_STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


class DiscordPluginInteractionBridge:
    """Translate the versioned plugin contract into discord.py message kwargs."""

    def __init__(self, adapter: Any):
        self._adapter = adapter

    def build_message_kwargs(self, plugin_id: str, spec: dict) -> dict:
        """Build native Discord kwargs while keeping routing host-owned."""
        normalized = validate_message_spec(spec)
        embeds = []
        for embed_spec in normalized["embeds"]:
            if not isinstance(embed_spec, dict):
                raise ValueError("Invalid Discord message spec")
            if any(
                key in embed_spec and not isinstance(embed_spec[key], str)
                for key in ("title", "description")
            ) or (
                "color" in embed_spec
                and (
                    isinstance(embed_spec["color"], bool)
                    or not isinstance(embed_spec["color"], int)
                )
            ):
                raise ValueError("Invalid Discord message spec")
            fields = embed_spec.get("fields", [])
            if not isinstance(fields, list):
                raise ValueError("Invalid Discord message spec")
            embed_kwargs = {
                key: embed_spec[key]
                for key in ("title", "description", "color")
                if key in embed_spec
            }
            embed = discord.Embed(**embed_kwargs)
            for field in fields:
                if (
                    not isinstance(field, dict)
                    or not isinstance(field.get("name"), str)
                    or not isinstance(field.get("value"), str)
                    or (
                        "inline" in field
                        and not isinstance(field["inline"], bool)
                    )
                ):
                    raise ValueError("Invalid Discord message spec")
                embed.add_field(
                    name=field["name"],
                    value=field["value"],
                    inline=field.get("inline", False),
                )
            embeds.append(embed)

        view = discord.ui.View(timeout=None)
        for component in normalized["components"]:
            button = discord.ui.Button(
                label=component["label"],
                style=_STYLE_MAP[component["style"]],
                custom_id=encode_custom_id(
                    plugin_id,
                    component["action"],
                    component["route_token"],
                ),
                disabled=component["disabled"],
            )
            if "value" in component:
                button.value = component["value"]
            view.add_item(button)

        return {"content": normalized["content"], "embeds": embeds, "view": view}
