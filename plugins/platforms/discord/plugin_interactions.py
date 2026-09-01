"""Render validated plugin interaction specs with native Discord objects."""

from __future__ import annotations

import asyncio
from typing import Any

import discord

from hermes_cli.discord_interactions import (
    CAPABILITY_ID,
    decode_custom_id,
    encode_custom_id,
    validate_interaction_result,
    validate_message_spec,
)
from hermes_cli.plugin_capabilities import plugin_capability_granted
from hermes_cli.plugins import get_plugin_manager
from plugins.platforms.discord.adapter import _component_check_auth

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

    async def handle_interaction(self, interaction: Any) -> bool:
        """Route one host-owned Discord interaction."""
        data = getattr(interaction, "data", None) or {}
        custom_id = str(data.get("custom_id", ""))
        if not custom_id.startswith("hdi1."):
            return False
        try:
            route = decode_custom_id(custom_id)
        except ValueError:
            await self._ephemeral_once(interaction, "유효하지 않은 요청이야.")
            return True
        if not _component_check_auth(
            interaction,
            self._adapter._allowed_user_ids,
            self._adapter._allowed_role_ids,
        ):
            await self._ephemeral_once(interaction, "허용된 사용자가 아니야.")
            return True
        handler = get_plugin_manager().get_discord_interaction_handler(
            route["plugin_id"]
        )
        if handler is None or not plugin_capability_granted(
            route["plugin_id"], CAPABILITY_ID
        ):
            await self._ephemeral_once(interaction, "현재 사용할 수 없는 기능이야.")
            return True
        payload = self._normalize_interaction(interaction, route)
        is_modal_open = payload["kind"] == "button" and route["action"].startswith(
            "open_"
        )
        if not is_modal_open and not await self._defer_once(interaction):
            return True
        timeout = 2.0 if is_modal_open else 90.0
        result = await asyncio.wait_for(handler(payload), timeout=timeout)
        normalized = validate_interaction_result(
            result, interaction_kind=payload["kind"]
        )
        if is_modal_open:
            await self._apply_undeferred_result(interaction, route, normalized)
        else:
            await self._apply_deferred_result(interaction, route, normalized)
        return True

    @staticmethod
    async def _defer_once(interaction: Any) -> bool:
        if interaction.response.is_done():
            return False
        await interaction.response.defer()
        return True

    @staticmethod
    async def _ephemeral_once(interaction: Any, content: str) -> None:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True)

    @staticmethod
    def _normalize_interaction(interaction: Any, route: dict[str, str]) -> dict:
        payload = {
            "api_version": 1,
            "interaction_id": str(interaction.id),
            "kind": "button",
            "plugin_id": route["plugin_id"],
            "action": route["action"],
            "route_token": route["route_token"],
            "user_id": str(interaction.user.id),
            "guild_id": str(interaction.guild_id),
            "channel_id": str(interaction.channel_id),
            "message_id": str(interaction.message.id),
            "modal_values": {},
        }
        if "component_value" in route:
            payload["component_value"] = route["component_value"]
        return payload

    @staticmethod
    def _build_modal(
        plugin_id: str, route_token: str, spec: dict[str, Any]
    ) -> discord.ui.Modal:
        modal = discord.ui.Modal(
            title=spec["title"],
            custom_id=encode_custom_id(plugin_id, spec["action"], route_token),
        )
        styles = {
            "short": discord.TextStyle.short,
            "paragraph": discord.TextStyle.paragraph,
        }
        for field in spec["fields"]:
            modal.add_item(
                discord.ui.TextInput(
                    custom_id=field["id"],
                    label=field["label"],
                    style=styles[field["style"]],
                    required=field["required"],
                    min_length=field["min_length"],
                    max_length=field["max_length"],
                )
            )
        return modal

    async def _apply_undeferred_result(
        self,
        interaction: Any,
        route: dict[str, str],
        result: dict[str, Any],
    ) -> None:
        if result["kind"] == "open_modal" and not interaction.response.is_done():
            await interaction.response.send_modal(
                self._build_modal(
                    route["plugin_id"], route["route_token"], result["modal"]
                )
            )

    async def _apply_deferred_result(
        self,
        interaction: Any,
        route: dict[str, str],
        result: dict[str, Any],
    ) -> None:
        if result["kind"] == "update_message":
            await interaction.edit_original_response(
                **self.build_message_kwargs(route["plugin_id"], result["message"])
            )

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
                    component.get("value"),
                ),
                disabled=component["disabled"],
            )
            view.add_item(button)

        return {"content": normalized["content"], "embeds": embeds, "view": view}
