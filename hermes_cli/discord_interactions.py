"""Versioned JSON contract for Discord plugin interactions."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

CAPABILITY_ID = "gateway.discord_interactions"
INTERACTIONS_CONTRACT_VERSION = 1

_PREFIX = "hdi1"
_ACTION_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,43}$")
_PLUGIN_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ID_RE = re.compile(r"^[0-9]+$")
_BUTTON_STYLES = frozenset({"primary", "secondary", "success", "danger"})
_ERROR = "Invalid Discord plugin custom ID"
_MESSAGE_ERROR = "Invalid Discord message spec"
_MODAL_ERROR = "Invalid Discord Modal spec"
_ADAPTER_ERROR_CODES = frozenset(
    {"invalid_argument", "channel_not_found", "message_not_found", "forbidden", "discord_error"}
)


def _error(code: str) -> dict[str, object]:
    return {"ok": False, "error_code": code}


def _normalize_failure_receipt(receipt: object) -> dict[str, object] | None:
    if not isinstance(receipt, dict) or receipt.get("ok") is not False:
        return None
    error_code = receipt.get("error_code")
    if not isinstance(error_code, str) or error_code not in _ADAPTER_ERROR_CODES:
        return None
    return _error(error_code)


def _normalize_send_receipt(receipt: object, channel_id: str) -> dict[str, object]:
    failure = _normalize_failure_receipt(receipt)
    if failure is not None:
        return failure
    if (
        not isinstance(receipt, dict)
        or receipt.get("ok") is not True
        or receipt.get("error_code") != ""
        or receipt.get("channel_id") != channel_id
        or not isinstance(receipt.get("message_id"), str)
        or not _ID_RE.fullmatch(receipt["message_id"])
    ):
        return _error("invalid_adapter_receipt")
    return {
        "ok": True,
        "channel_id": channel_id,
        "message_id": receipt["message_id"],
        "error_code": "",
    }


def _normalize_update_receipt(receipt: object) -> dict[str, object]:
    failure = _normalize_failure_receipt(receipt)
    if failure is not None:
        return failure
    if (
        not isinstance(receipt, dict)
        or receipt.get("ok") is not True
        or receipt.get("error_code") != ""
    ):
        return _error("invalid_adapter_receipt")
    return {"ok": True, "error_code": ""}


class DiscordInteractions:
    """Per-plugin facade for Discord interaction message I/O."""

    def __init__(self, plugin_id: str):
        self._plugin_id = plugin_id

    def _capability_granted(self) -> bool:
        try:
            from hermes_cli.plugin_capabilities import plugin_capability_granted

            return plugin_capability_granted(self._plugin_id, CAPABILITY_ID)
        except Exception:
            return False

    @staticmethod
    def _resolve_adapter() -> tuple[Any | None, dict[str, object] | None]:
        from gateway.config import Platform

        try:
            from gateway.run import _gateway_runner_ref

            runner = _gateway_runner_ref()
        except Exception:
            runner = None
        if runner is None:
            return None, _error("gateway_unavailable")
        adapter = getattr(runner, "adapters", {}).get(Platform.DISCORD)
        if adapter is None:
            return None, _error("adapter_not_registered")
        try:
            connected = bool(adapter.is_connected)
        except Exception:
            connected = False
        if not connected:
            return None, _error("adapter_disconnected")
        return adapter, None

    def _gate(self, **ids: object) -> tuple[Any | None, dict[str, object] | None]:
        if not self._capability_granted():
            return None, _error("capability_not_granted")
        if any(
            not isinstance(value, str) or not _ID_RE.fullmatch(value)
            for value in ids.values()
        ):
            return None, _error("invalid_argument")
        return self._resolve_adapter()

    async def _call(
        self, adapter: Any, method_name: str, *args: object
    ) -> tuple[object, dict[str, object] | None]:
        try:
            operation = getattr(adapter, method_name)
            if not callable(operation):
                raise TypeError
            return await operation(*args), None
        except Exception:
            trace_id = uuid.uuid4().hex
            logger.error(
                "Discord interaction adapter call failed plugin=%s trace_id=%s",
                self._plugin_id,
                trace_id,
            )
            return None, _error("adapter_error")

    async def send(self, channel_id: str, spec: dict) -> dict:
        """Send one validated Discord interaction message."""
        adapter, error = self._gate(channel_id=channel_id)
        if error is not None or adapter is None:
            return error or _error("gateway_unavailable")
        try:
            normalized = validate_message_spec(spec)
        except ValueError:
            return _error("invalid_argument")
        receipt, error = await self._call(
            adapter,
            "plugin_interaction_send",
            self._plugin_id,
            channel_id,
            normalized,
        )
        if error is not None:
            return error
        return _normalize_send_receipt(receipt, channel_id)

    async def update(self, channel_id: str, message_id: str, spec: dict) -> dict:
        """Update one validated Discord interaction message."""
        adapter, error = self._gate(channel_id=channel_id, message_id=message_id)
        if error is not None or adapter is None:
            return error or _error("gateway_unavailable")
        try:
            normalized = validate_message_spec(spec)
        except ValueError:
            return _error("invalid_argument")
        receipt, error = await self._call(
            adapter,
            "plugin_interaction_update",
            self._plugin_id,
            channel_id,
            message_id,
            normalized,
        )
        if error is not None:
            return error
        return _normalize_update_receipt(receipt)


def encode_custom_id(
    plugin_id: str,
    action: str,
    route_token: str,
    component_value: str | None = None,
) -> str:
    """Encode the host-owned plugin namespace and opaque routing fields."""
    if (
        not isinstance(plugin_id, str)
        or not plugin_id
        or not isinstance(action, str)
        or not _ACTION_RE.fullmatch(action)
        or not isinstance(route_token, str)
        or not _TOKEN_RE.fullmatch(route_token)
        or (component_value is not None and not isinstance(component_value, str))
        or component_value == ""
    ):
        raise ValueError(_ERROR)
    try:
        encoded_plugin = base64.urlsafe_b64encode(plugin_id.encode("utf-8")).decode(
            "ascii"
        ).rstrip("=")
        encoded_value = (
            base64.urlsafe_b64encode(component_value.encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
            if component_value is not None
            else None
        )
    except UnicodeError as exc:
        raise ValueError(_ERROR) from exc
    segments = [_PREFIX, encoded_plugin, action, route_token]
    if encoded_value is not None:
        segments.append(encoded_value)
    custom_id = ".".join(segments)
    if len(custom_id) > 90:
        raise ValueError(_ERROR)
    return custom_id


def decode_custom_id(custom_id: str) -> dict[str, str]:
    """Decode a v1 custom ID, rejecting every non-canonical representation."""
    if not isinstance(custom_id, str) or len(custom_id) > 90:
        raise ValueError(_ERROR)
    segments = custom_id.split(".")
    if len(segments) not in (4, 5):
        raise ValueError(_ERROR)
    prefix, encoded_plugin, action, route_token = segments[:4]
    encoded_value = segments[4] if len(segments) == 5 else None
    if (
        prefix != _PREFIX
        or not _PLUGIN_B64_RE.fullmatch(encoded_plugin)
        or not _ACTION_RE.fullmatch(action)
        or not _TOKEN_RE.fullmatch(route_token)
        or (
            encoded_value is not None
            and not _PLUGIN_B64_RE.fullmatch(encoded_value)
        )
    ):
        raise ValueError(_ERROR)
    try:
        padding = "=" * (-len(encoded_plugin) % 4)
        plugin_bytes = base64.b64decode(
            encoded_plugin + padding, altchars=b"-_", validate=True
        )
        plugin_id = plugin_bytes.decode("utf-8")
    except (binascii.Error, UnicodeError) as exc:
        raise ValueError(_ERROR) from exc
    if (
        not plugin_id
        or base64.urlsafe_b64encode(plugin_bytes).decode("ascii").rstrip("=")
        != encoded_plugin
    ):
        raise ValueError(_ERROR)
    decoded = {
        "plugin_id": plugin_id,
        "action": action,
        "route_token": route_token,
    }
    if encoded_value is not None:
        try:
            padding = "=" * (-len(encoded_value) % 4)
            value_bytes = base64.b64decode(
                encoded_value + padding, altchars=b"-_", validate=True
            )
            component_value = value_bytes.decode("utf-8")
        except (binascii.Error, UnicodeError) as exc:
            raise ValueError(_ERROR) from exc
        if (
            not component_value
            or base64.urlsafe_b64encode(value_bytes).decode("ascii").rstrip("=")
            != encoded_value
        ):
            raise ValueError(_ERROR)
        decoded["component_value"] = component_value
    return decoded


def _json_copy(value: object, error: str) -> object:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc


def _text_size(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_size(item) for item in value)
    if isinstance(value, dict):
        return sum(_text_size(item) for item in value.values())
    return 0


def validate_message_spec(spec: dict) -> dict:
    """Validate and copy a version-one Discord message specification."""
    normalized = _json_copy(spec, _MESSAGE_ERROR)
    if (
        not isinstance(normalized, dict)
        or set(normalized) != {"api_version", "content", "embeds", "components"}
        or normalized["api_version"] != INTERACTIONS_CONTRACT_VERSION
        or isinstance(normalized["api_version"], bool)
        or not isinstance(normalized["content"], str)
        or not isinstance(normalized["embeds"], list)
        or not isinstance(normalized["components"], list)
        or len(normalized["components"]) > 25
    ):
        raise ValueError(_MESSAGE_ERROR)

    for component in normalized["components"]:
        if not isinstance(component, dict):
            raise ValueError(_MESSAGE_ERROR)
        keys = set(component)
        required = {"type", "action", "route_token", "label", "style", "disabled"}
        if (
            not required.issubset(keys)
            or not keys.issubset(required | {"value"})
            or component["type"] != "button"
            or not isinstance(component["action"], str)
            or not _ACTION_RE.fullmatch(component["action"])
            or not isinstance(component["route_token"], str)
            or not _TOKEN_RE.fullmatch(component["route_token"])
            or not isinstance(component["label"], str)
            or not 1 <= len(component["label"]) <= 70
            or not isinstance(component["style"], str)
            or component["style"] not in _BUTTON_STYLES
            or type(component["disabled"]) is not bool
            or (
                "value" in component
                and (
                    not isinstance(component["value"], str)
                    or len(component["value"]) > 200
                )
            )
        ):
            raise ValueError(_MESSAGE_ERROR)

    visible = normalized["content"] + "".join(
        component["label"] for component in normalized["components"]
    )
    if len(visible) + _text_size(normalized["embeds"]) > 4_000:
        raise ValueError(_MESSAGE_ERROR)
    return normalized


def validate_modal_spec(spec: dict) -> dict:
    """Validate and copy a version-one Discord Modal specification."""
    normalized = _json_copy(spec, _MODAL_ERROR)
    if (
        not isinstance(normalized, dict)
        or set(normalized) != {"api_version", "action", "title", "fields"}
        or normalized["api_version"] != INTERACTIONS_CONTRACT_VERSION
        or isinstance(normalized["api_version"], bool)
        or not isinstance(normalized["action"], str)
        or not _ACTION_RE.fullmatch(normalized["action"])
        or not isinstance(normalized["title"], str)
        or not 1 <= len(normalized["title"]) <= 45
        or not isinstance(normalized["fields"], list)
        or len(normalized["fields"]) > 5
    ):
        raise ValueError(_MODAL_ERROR)

    field_ids: set[str] = set()
    expected_keys = {
        "id",
        "label",
        "style",
        "required",
        "min_length",
        "max_length",
    }
    for field in normalized["fields"]:
        if not isinstance(field, dict) or set(field) != expected_keys:
            raise ValueError(_MODAL_ERROR)
        style = field["style"]
        max_budget = 300 if style == "short" else 2_000
        if (
            not isinstance(field["id"], str)
            or not 1 <= len(field["id"]) <= 100
            or field["id"] in field_ids
            or not isinstance(field["label"], str)
            or not 1 <= len(field["label"]) <= 45
            or not isinstance(style, str)
            or style not in {"short", "paragraph"}
            or type(field["required"]) is not bool
            or type(field["min_length"]) is not int
            or type(field["max_length"]) is not int
            or not 0 <= field["min_length"] <= field["max_length"]
            or not 1 <= field["max_length"] <= max_budget
        ):
            raise ValueError(_MODAL_ERROR)
        field_ids.add(field["id"])
    return normalized


def validate_interaction_result(result: dict, *, interaction_kind: str) -> dict:
    """Validate and copy a result for a button or Modal submission."""
    error = "Invalid Discord interaction result"
    normalized = _json_copy(result, error)
    if not isinstance(normalized, dict):
        raise ValueError(error)
    kind = normalized.get("kind")
    if not isinstance(kind, str):
        raise ValueError(error)
    if kind == "open_modal" and interaction_kind != "button":
        raise ValueError("open_modal is only valid for a button interaction")
    if interaction_kind not in {"button", "modal_submit"}:
        raise ValueError(error)

    expected_keys = {
        "open_modal": {"kind", "modal"},
        "update_message": {"kind", "message"},
        "ephemeral": {"kind", "content"},
        "no_change": {"kind"},
    }
    if kind not in expected_keys or set(normalized) != expected_keys[kind]:
        raise ValueError(error)
    try:
        if kind == "open_modal":
            normalized["modal"] = validate_modal_spec(normalized["modal"])
        elif kind == "update_message":
            normalized["message"] = validate_message_spec(normalized["message"])
        elif kind == "ephemeral" and (
            not isinstance(normalized["content"], str)
            or not 1 <= len(normalized["content"]) <= 2_000
        ):
            raise ValueError(error)
    except ValueError as exc:
        raise ValueError(error) from exc
    return normalized
