"""Versioned JSON contract for Discord plugin interactions."""

from __future__ import annotations

import base64
import binascii
import json
import re

CAPABILITY_ID = "gateway.discord_interactions"
INTERACTIONS_CONTRACT_VERSION = 1

_PREFIX = "hdi1"
_ACTION_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,43}$")
_PLUGIN_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_BUTTON_STYLES = frozenset({"primary", "secondary", "success", "danger"})
_ERROR = "Invalid Discord plugin custom ID"
_MESSAGE_ERROR = "Invalid Discord message spec"
_MODAL_ERROR = "Invalid Discord Modal spec"


def encode_custom_id(plugin_id: str, action: str, route_token: str) -> str:
    """Encode the host-owned plugin namespace and opaque routing fields."""
    if (
        not isinstance(plugin_id, str)
        or not plugin_id
        or not isinstance(action, str)
        or not _ACTION_RE.fullmatch(action)
        or not isinstance(route_token, str)
        or not _TOKEN_RE.fullmatch(route_token)
    ):
        raise ValueError(_ERROR)
    try:
        encoded_plugin = base64.urlsafe_b64encode(plugin_id.encode("utf-8")).decode(
            "ascii"
        ).rstrip("=")
    except UnicodeError as exc:
        raise ValueError(_ERROR) from exc
    value = ".".join((_PREFIX, encoded_plugin, action, route_token))
    if len(value) > 90:
        raise ValueError(_ERROR)
    return value


def decode_custom_id(custom_id: str) -> dict[str, str]:
    """Decode a v1 custom ID, rejecting every non-canonical representation."""
    if not isinstance(custom_id, str) or len(custom_id) > 90:
        raise ValueError(_ERROR)
    segments = custom_id.split(".")
    if len(segments) != 4:
        raise ValueError(_ERROR)
    prefix, encoded_plugin, action, route_token = segments
    if (
        prefix != _PREFIX
        or not _PLUGIN_B64_RE.fullmatch(encoded_plugin)
        or not _ACTION_RE.fullmatch(action)
        or not _TOKEN_RE.fullmatch(route_token)
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
    return {
        "plugin_id": plugin_id,
        "action": action,
        "route_token": route_token,
    }


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
