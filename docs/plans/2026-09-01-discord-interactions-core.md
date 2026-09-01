# Discord Plugin Interactions Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a capability-gated, versioned Discord button/Modal seam and process-safe plugin-state CAS without adding quiz domain logic to Hermes core.

**Architecture:** `PluginState.compare_and_set()` supplies one generic transaction primitive. `PluginContext` owns one handler registration per canonical plugin ID and exposes a `DiscordInteractions` facade; the Discord adapter alone translates versioned dict specs to `discord.py`, performs allowlist/ACK policy, and routes stable custom IDs back to the registered handler. The interface is deliberately Discord-only and uses existing capability, ownership-ledger, gateway-runner, and component-auth machinery.

**Tech Stack:** Python 3.11, stdlib (`asyncio`, `base64`, `hmac`, `json`, `re`, `secrets`, `typing`), existing `discord.py`, existing Hermes plugin/capability/gateway modules, pytest + pytest-asyncio.

**Spec:** `docs/rfcs/2026-09-discord-plugin-interactions-cs-quiz.md`

## Global Constraints

- Do not add quiz types, scoring, prompts, or learning state to core.
- Do not create a cross-platform interaction abstraction.
- Do not expose Discord SDK objects to plugins.
- Public plugin payloads are JSON-serializable dicts with `api_version: 1`.
- Capability `gateway.discord_interactions` is default-off and rechecked at registration, dispatch, send, and update.
- Custom IDs are at most 90 characters; malformed IDs fail before plugin callback.
- Button `open_modal` callbacks have a 2-second host timeout; deferred callbacks have a 90-second host timeout.
- Reuse `_component_check_auth()` for Discord admission and `PluginRegistration` for unload/reload cleanup.
- No new dependency.
- Work in an isolated git worktree when execution starts.

## File Map

- Modify `hermes_cli/plugins.py`: generic CAS, context facade property, handler registration, manager registry/accessor/cleanup.
- Modify `hermes_cli/plugin_capabilities.py`: canonical capability declaration.
- Create `hermes_cli/discord_interactions.py`: v1 codec, validators, capability-gated facade, stable result envelopes.
- Create `plugins/platforms/discord/plugin_interactions.py`: `discord.py` spec renderer and exactly-once ACK router.
- Modify `plugins/platforms/discord/adapter.py`: construct the bridge, register `on_interaction`, expose internal send/update methods.
- Modify `tests/hermes_cli/test_plugin_config_state_bridge.py`: CAS unit/thread/process coverage.
- Create `tests/hermes_cli/test_discord_interactions.py`: codec, validation, capability, registration, facade coverage.
- Create `tests/gateway/test_discord_plugin_interactions.py`: ACK, Modal, routing, REST fallback, exception/timeout coverage.
- Re-run existing Discord regression files unchanged.

---

### Task 1: Generic process-safe plugin-state CAS

**Files:**
- Modify: `hermes_cli/plugins.py:1320-1390`
- Modify: `tests/hermes_cli/test_plugin_config_state_bridge.py:187-278`

**Interfaces:**
- Consumes: existing `_locked_plugin_state()`, `PluginState._read_unlocked()`, `utils.atomic_json_write()`.
- Produces: `PluginState.compare_and_set(key: str, expected: Any, value: Any) -> bool`.
- Produces: `PluginState.get_backup(key: str, default: Any = None) -> Any` for read-only recovery inspection.

- [ ] **Step 1: Add a failing equality and mismatch test**

```python
def test_state_compare_and_set_commits_only_matching_snapshot(isolated_home: Path) -> None:
    state = _context().state
    state.set("quiz", {"revision": 1, "value": "old"})

    assert state.compare_and_set(
        "quiz",
        expected={"revision": 1, "value": "old"},
        value={"revision": 2, "value": "new"},
    ) is True
    assert state.compare_and_set(
        "quiz",
        expected={"revision": 1, "value": "old"},
        value={"revision": 3, "value": "lost"},
    ) is False
    assert state.get("quiz") == {"revision": 2, "value": "new"}
```

- [ ] **Step 2: Run the single test and verify RED**

Run:

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugin_config_state_bridge.py::test_state_compare_and_set_commits_only_matching_snapshot
```

Expected: FAIL with `AttributeError: 'PluginState' object has no attribute 'compare_and_set'`.

- [ ] **Step 3: Extract one locked writer, preserve the previous-good snapshot, and implement CAS**

Implement this shape in `PluginState`; both `set()` and CAS call `_write_unlocked(previous, value)` while holding the existing cross-process lock so quota, backup, and serialization cannot drift. The backup path is `self.data_dir / "_백업_원본_state.json"` and only one generation exists.

```python
def _write_unlocked(
    self,
    previous: dict[str, Any],
    data: dict[str, Any],
) -> None:
    try:
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Plugin state is not JSON-serializable") from exc
    if len(encoded) > self.quota_bytes:
        raise ValueError(
            f"Plugin state quota exceeded: {len(encoded)} bytes is greater "
            f"than the {self.quota_bytes}-byte per-plugin quota"
        )
    from utils import atomic_json_write
    if self.path.exists():
        atomic_json_write(self.data_dir / "_백업_원본_state.json", previous, mode=0o600)
    atomic_json_write(self.path, data, mode=0o600)


def compare_and_set(self, key: str, expected: Any, value: Any) -> bool:
    self._validate_key(key)
    with _locked_plugin_state(self.path):
        data = self._read_unlocked()
        if data.get(key) != expected:
            return False
        next_data = dict(data)
        next_data[key] = value
        self._write_unlocked(data, next_data)
        return True
```

Change `set()` to preserve `previous = dict(data)`, set the key, then call `_write_unlocked(previous, data)` under the same existing lock. Implement `get_backup()` by validating the key, taking the same lock, parsing `_백업_원본_state.json`, and returning that snapshot's key/default without modifying primary state.

- [ ] **Step 4: Add absent-key, quota, and corruption tests**

```python
def test_state_compare_and_set_can_claim_absent_key(isolated_home: Path) -> None:
    state = _context().state
    assert state.compare_and_set("quiz", expected=None, value={"revision": 1}) is True
    assert state.get("quiz") == {"revision": 1}


def test_state_compare_and_set_quota_failure_preserves_file(isolated_home: Path) -> None:
    state = _context().state
    state.set("quiz", {"revision": 1})
    before = state.path.read_bytes()
    with pytest.raises(ValueError, match="quota"):
        state.compare_and_set("quiz", {"revision": 1}, "x" * (state.quota_bytes + 1))
    assert state.path.read_bytes() == before


def test_state_compare_and_set_refuses_corrupt_source(isolated_home: Path) -> None:
    state = _context().state
    state.data_dir.mkdir(parents=True)
    state.path.write_text('{"quiz":', encoding="utf-8")
    with pytest.raises(RuntimeError, match="Cannot parse plugin state"):
        state.compare_and_set("quiz", None, {"revision": 1})
    assert state.path.read_text(encoding="utf-8") == '{"quiz":'


def test_state_backup_is_exact_previous_good_generation(isolated_home: Path) -> None:
    state = _context().state
    state.set("quiz", {"revision": 1})
    state.set("quiz", {"revision": 2})
    assert state.get_backup("quiz") == {"revision": 1}
    state.set("quiz", {"revision": 3})
    assert state.get_backup("quiz") == {"revision": 2}
    assert [p.name for p in state.data_dir.glob("_백업_원본_state*.json")] == [
        "_백업_원본_state.json"
    ]
```

- [ ] **Step 5: Add a two-process winner test**

Start two subprocesses against the same `HERMES_HOME`. Each waits for a barrier file, then calls CAS from revision 1 to a distinct winner value and prints only `1` or `0`. Assert exit codes `[0, 0]`, sorted output `['0', '1']`, final revision 2 with either winner, and `get_backup("quiz") == {"revision": 1}`. Reuse the subprocess/env pattern already present in `test_state_cross_process_lock_preserves_every_update`.

- [ ] **Step 6: Run the state bridge file and verify GREEN**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugin_config_state_bridge.py
```

Expected: all tests pass; no `.tmp` files remain.

- [ ] **Step 7: Commit the CAS task**

```bash
git add hermes_cli/plugins.py tests/hermes_cli/test_plugin_config_state_bridge.py
git commit -m "feat: add atomic compare-and-set for plugin state"
```

---

### Task 2: Versioned contract, custom-ID codec, capability, and registration

**Files:**
- Create: `hermes_cli/discord_interactions.py`
- Modify: `hermes_cli/plugin_capabilities.py:77-132`
- Modify: `hermes_cli/plugins.py:1393-1405, 1518-1533, 3400-3460, 3727-3733, 5471-5485`
- Create: `tests/hermes_cli/test_discord_interactions.py`

**Interfaces:**
- Consumes: `plugin_capability_granted()`, `PluginRegistration`, `PluginManager.scope_key`.
- Produces:
  - `CAPABILITY_ID = "gateway.discord_interactions"`
  - `INTERACTIONS_CONTRACT_VERSION = 1`
  - `encode_custom_id(plugin_id: str, action: str, route_token: str) -> str`
  - `decode_custom_id(custom_id: str) -> dict[str, str]`
  - `validate_message_spec(spec: dict) -> dict`
  - `validate_modal_spec(spec: dict) -> dict`
  - `validate_interaction_result(result: dict, *, interaction_kind: str) -> dict`
  - `PluginContext.register_discord_interaction(handler: Callable) -> PluginRegistration`
  - `PluginManager.get_discord_interaction_handler(plugin_id: str) -> Callable | None`

- [ ] **Step 1: Write failing codec and validator tests**

```python
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


def test_custom_id_round_trip_and_limit() -> None:
    custom_id = encode_custom_id("cs-quiz", "submit_choice", "A" * 32)
    assert len(custom_id) <= 90
    assert decode_custom_id(custom_id) == {
        "plugin_id": "cs-quiz",
        "action": "submit_choice",
        "route_token": "A" * 32,
    }

@pytest.mark.parametrize("raw", ["", "other:cs-quiz:x:y", "hdi1:bad", "hdi1:::x"])
def test_custom_id_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError, match="custom ID"):
        decode_custom_id(raw)


def test_open_modal_is_button_only() -> None:
    result = {
        "kind": "open_modal",
        "modal": {"api_version": 1, "action": "submit", "title": "답변", "fields": []},
    }
    with pytest.raises(ValueError, match="button"):
        validate_interaction_result(result, interaction_kind="modal_submit")
```

- [ ] **Step 2: Run the new file and verify RED**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py
```

Expected: import failure for `hermes_cli.discord_interactions`.

- [ ] **Step 3: Implement the v1 codec with strict alphabets**

Use `hdi1.<plugin_b64>.<action>.<route_token>`. Encode plugin ID with URL-safe base64 and strip `=`; decode with restored padding. Require action `[a-z0-9_]{1,32}`, route token `[A-Za-z0-9_-]{16,43}`, non-empty plugin ID, UTF-8 decode, and total length at most 90. Use `hmac.compare_digest()` only where equality checks protect opaque tokens; do not claim the custom ID is signed.

```python
_PREFIX = "hdi1"
_ACTION_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,43}$")


def encode_custom_id(plugin_id: str, action: str, route_token: str) -> str:
    encoded_plugin = base64.urlsafe_b64encode(plugin_id.encode("utf-8")).decode("ascii").rstrip("=")
    value = ".".join((_PREFIX, encoded_plugin, action, route_token))
    if not _ACTION_RE.fullmatch(action) or not _TOKEN_RE.fullmatch(route_token) or len(value) > 90:
        raise ValueError("Invalid Discord plugin custom ID")
    return value
```

- [ ] **Step 4: Implement strict spec/result validation**

Reject unknown top-level keys. Enforce RFC internal budgets: message `api_version == 1`, components are buttons only, every button has `route_token` matching `[A-Za-z0-9_-]{16,43}`, labels at most 70, custom ID generated by host, Modal title/field labels within Discord limits, short input max 300, paragraph max 2,000, and result kind in `open_modal|update_message|ephemeral|no_change`. Return a copied normalized dict; never mutate plugin input.

- [ ] **Step 5: Add capability and registration tests**

```python
def test_discord_interactions_capability_is_registered() -> None:
    spec = CAPABILITY_REGISTRY["gateway.discord_interactions"]
    assert spec.legacy_path == ("allow_discord_interactions",)
    assert "Discord" in spec.description


def test_registration_is_default_off(monkeypatch) -> None:
    ctx = _context(name="cs-quiz")
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda plugin_id, capability: False,
    )
    with pytest.raises(PermissionError, match="gateway.discord_interactions"):
        ctx.register_discord_interaction(lambda interaction: {"kind": "no_change"})


def test_registration_collision_and_dispose(monkeypatch) -> None:
    manager = PluginManager()
    monkeypatch.setattr(
        "hermes_cli.plugin_capabilities.plugin_capability_granted",
        lambda plugin_id, capability: True,
    )
    first = PluginContext(PluginManifest(name="cs-quiz"), manager)
    second = PluginContext(PluginManifest(name="cs-quiz"), manager)
    handle = first.register_discord_interaction(lambda interaction: {"kind": "no_change"})
    with pytest.raises(ValueError, match="already registered"):
        second.register_discord_interaction(lambda interaction: {"kind": "no_change"})
    handle.dispose()
    assert manager.get_discord_interaction_handler("cs-quiz") is None
```

- [ ] **Step 6: Implement capability and manager-local ownership**

Add to `CAPABILITY_REGISTRY`:

```python
CapabilitySpec(
    id="gateway.discord_interactions",
    legacy_path=("allow_discord_interactions",),
    description=(
        "Register Discord button and Modal callbacks and send or update "
        "interactive messages as the connected gateway bot"
    ),
)
```

Store manager entries as `plugin_id -> handler`. `register_discord_interaction()` checks callable, capability, and collision, then calls `_track()` with a release closure that removes only the identical handler. Clear the manager map in full unload. Do not add a process-global registry.

- [ ] **Step 7: Run core contract tests and verify GREEN**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py tests/hermes_cli/test_plugin_capabilities.py
```

- [ ] **Step 8: Commit the contract task**

```bash
git add hermes_cli/discord_interactions.py hermes_cli/plugin_capabilities.py hermes_cli/plugins.py tests/hermes_cli/test_discord_interactions.py
git commit -m "feat: add capability-gated Discord interaction contract"
```

---

### Task 3: Capability-gated `ctx.discord` send/update facade

**Files:**
- Modify: `hermes_cli/discord_interactions.py`
- Modify: `hermes_cli/plugins.py:1396-1405, 1518-1533`
- Modify: `tests/hermes_cli/test_discord_interactions.py`

**Interfaces:**
- Consumes: gateway runner adapter registry, Task 2 validators, capability check.
- Produces:
  - `DiscordInteractions(plugin_id: str)`
  - `async send(channel_id: str, spec: dict) -> dict`
  - `async update(channel_id: str, message_id: str, spec: dict) -> dict`
  - `PluginContext.discord` cached property.

- [ ] **Step 1: Write failing facade gate and receipt tests**

```python
@pytest.mark.asyncio
async def test_send_rechecks_capability_and_returns_receipt(monkeypatch) -> None:
    adapter = SimpleNamespace(
        is_connected=True,
        plugin_interaction_send=AsyncMock(
            return_value={"ok": True, "channel_id": "10", "message_id": "20", "error_code": ""}
        ),
    )
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: SimpleNamespace(adapters={Platform.DISCORD: adapter}))
    monkeypatch.setattr("hermes_cli.plugin_capabilities.plugin_capability_granted", lambda *_: True)
    result = await DiscordInteractions("cs-quiz").send("10", valid_message_spec())
    assert result == {"ok": True, "channel_id": "10", "message_id": "20", "error_code": ""}

@pytest.mark.asyncio
async def test_update_revocation_fails_before_adapter(monkeypatch) -> None:
    monkeypatch.setattr("hermes_cli.plugin_capabilities.plugin_capability_granted", lambda *_: False)
    result = await DiscordInteractions("cs-quiz").update("10", "20", valid_message_spec())
    assert result == {"ok": False, "error_code": "capability_not_granted"}
```

- [ ] **Step 2: Run the two tests and verify RED**

Run the exact node IDs with the Task 2 pytest command. Expected: missing `DiscordInteractions` or missing methods.

- [ ] **Step 3: Implement one deep facade**

The facade validates string IDs and specs, rechecks capability, resolves `Platform.DISCORD` from `_gateway_runner_ref().adapters`, requires `adapter.is_connected`, calls only `plugin_interaction_send/update`, catches adapter exceptions, logs a generated trace ID, and returns stable envelopes without exception text.

```python
class DiscordInteractions:
    def __init__(self, plugin_id: str):
        self._plugin_id = plugin_id

    async def send(self, channel_id: str, spec: dict) -> dict:
        adapter, error = self._gate(channel_id=channel_id)
        if error:
            return error
        return await self._call(adapter.plugin_interaction_send, channel_id, validate_message_spec(spec))

    async def update(self, channel_id: str, message_id: str, spec: dict) -> dict:
        adapter, error = self._gate(channel_id=channel_id, message_id=message_id)
        if error:
            return error
        return await self._call(
            adapter.plugin_interaction_update,
            channel_id,
            message_id,
            validate_message_spec(spec),
        )
```

- [ ] **Step 4: Wire and test `PluginContext.discord`**

Cache one `DiscordInteractions(self.plugin_id)` exactly as `platform_actions` is cached. Test same-object identity and canonical key binding.

- [ ] **Step 5: Run the facade file and verify GREEN**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py
```

- [ ] **Step 6: Commit the facade task**

```bash
git add hermes_cli/discord_interactions.py hermes_cli/plugins.py tests/hermes_cli/test_discord_interactions.py
git commit -m "feat: expose Discord interaction send and update facade"
```

---

### Task 4: Discord spec renderer and cache-independent send/update

**Files:**
- Create: `plugins/platforms/discord/plugin_interactions.py`
- Modify: `plugins/platforms/discord/adapter.py:1318-1330, 3429-4025`
- Create: `tests/gateway/test_discord_plugin_interactions.py`

**Interfaces:**
- Consumes: v1 validators/codec and adapter `_client`.
- Produces:
  - `DiscordPluginInteractionBridge(adapter)`
  - `build_message_kwargs(plugin_id: str, spec: dict) -> dict`
  - `DiscordPlatformAdapter.plugin_interaction_send(channel_id: str, spec: dict) -> dict`
  - `DiscordPlatformAdapter.plugin_interaction_update(channel_id: str, message_id: str, spec: dict) -> dict`.

- [ ] **Step 1: Write failing render and REST fallback tests**

Use fake `discord.Embed`, `discord.ui.View`, and `discord.ui.Button` objects or the existing import fixture pattern. Assert button custom IDs decode to the registering plugin, labels/styles/disabled flags are preserved, and no answer/rubric fields are synthesized.

For update, configure `client.get_channel()` to return `None`, `client.fetch_channel()` to return a channel, and `channel.fetch_message()` to return a message. Assert exact calls and `message.edit(**kwargs)`.

- [ ] **Step 2: Run gateway test file and verify RED**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
```

Expected: import failure for `plugins.platforms.discord.plugin_interactions`.

- [ ] **Step 3: Implement renderer using native Discord types**

Map only these v1 fields. For each button, pass `route_token` to `encode_custom_id(plugin_id, action, route_token)` and never expose the token as visible content:

```python
_STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}
```

Create an embed for each dict in `embeds`; use only `title`, `description`, `color`, and `fields` (`name`, `value`, `inline`). Build `discord.ui.View(timeout=None)`, create buttons with host-encoded custom IDs, and do not attach per-process callbacks. The adapter-level `on_interaction` listener added in Task 5 owns callbacks.

- [ ] **Step 4: Implement cache-independent channel/message resolution**

```python
async def _resolve_channel(client, channel_id: str):
    numeric = int(channel_id)
    return client.get_channel(numeric) or await client.fetch_channel(numeric)


async def plugin_interaction_update(self, channel_id: str, message_id: str, spec: dict) -> dict:
    try:
        channel = await _resolve_channel(self._client, channel_id)
        message = await channel.fetch_message(int(message_id))
        await message.edit(**self._plugin_interactions.build_message_kwargs(spec))
        return {"ok": True, "error_code": ""}
    except (TypeError, ValueError):
        return {"ok": False, "error_code": "invalid_argument"}
    except discord.NotFound:
        return {"ok": False, "error_code": "message_not_found"}
    except discord.Forbidden:
        return {"ok": False, "error_code": "forbidden"}
    except Exception:
        logger.exception("[Discord] Plugin interaction update failed")
        return {"ok": False, "error_code": "discord_error"}
```

Send returns `{ok, channel_id, message_id, error_code}` from the sent message receipt.

- [ ] **Step 5: Run render/send/update tests and verify GREEN**

Use the Task 4 pytest command. Confirm the cache-empty branch is covered.

- [ ] **Step 6: Commit renderer task**

```bash
git add plugins/platforms/discord/plugin_interactions.py plugins/platforms/discord/adapter.py tests/gateway/test_discord_plugin_interactions.py
git commit -m "feat: render plugin-owned Discord interaction messages"
```

---

### Task 5: Exactly-once ACK router for button and Modal submit

**Files:**
- Modify: `plugins/platforms/discord/plugin_interactions.py`
- Modify: `plugins/platforms/discord/adapter.py:1300-1400`
- Modify: `tests/gateway/test_discord_plugin_interactions.py`

**Interfaces:**
- Consumes: `get_plugin_manager()`, manager handler accessor, `_component_check_auth()`, Task 2 result validator.
- Produces: `DiscordPluginInteractionBridge.handle_interaction(interaction) -> bool`; returns `False` for non-Hermes custom IDs and `True` after owning the interaction.

- [ ] **Step 1: Add failing ACK matrix tests**

Cover these cases with `AsyncMock` response methods:

```text
open_short_answer button -> no defer, handler returns open_modal, send_modal once
submit_choice button -> defer once, handler once, edit_original_response once
modal_submit -> defer once, handler once
malformed prefix -> ephemeral once, handler never
unauthorized -> ephemeral once, handler never
unknown interaction during defer -> handler never
handler timeout/exception -> one deferred followup or edit with trace ID
modal_submit returning open_modal -> reject without second ACK
capability revoked after registration -> handler never
```

- [ ] **Step 2: Run the ACK tests and verify RED**

Run only the new test classes in `tests/gateway/test_discord_plugin_interactions.py`; expected missing `handle_interaction` or no listener behavior.

- [ ] **Step 3: Implement ACK-first routing**

```python
async def handle_interaction(self, interaction) -> bool:
    data = getattr(interaction, "data", None) or {}
    custom_id = str(data.get("custom_id", ""))
    if not custom_id.startswith("hdi1."):
        return False
    try:
        route = decode_custom_id(custom_id)
    except ValueError:
        await self._ephemeral_once(interaction, "유효하지 않은 문제야.")
        return True
    if not _component_check_auth(interaction, self.adapter._allowed_user_ids, self.adapter._allowed_role_ids):
        await self._ephemeral_once(interaction, "허용된 학습자가 아니야.")
        return True
    handler = get_plugin_manager().get_discord_interaction_handler(route["plugin_id"])
    if handler is None or not plugin_capability_granted(route["plugin_id"], CAPABILITY_ID):
        await self._ephemeral_once(interaction, "현재 사용할 수 없는 기능이야.")
        return True
    is_modal_open = route["action"] in {"open_short_answer", "open_essay"}
    if not is_modal_open:
        if not await self._defer_once(interaction):
            return True
    payload = self._normalize_interaction(interaction, route)
    timeout = 2.0 if is_modal_open else 90.0
    result = await asyncio.wait_for(handler(payload), timeout=timeout)
    await self._apply_result(
        interaction,
        validate_interaction_result(result, interaction_kind=payload["kind"]),
        inherited_route_token=route["route_token"],
    )
    return True
```

`_defer_once()` catches Discord's unknown-interaction response error and returns `False`; no callback follows that failure. `_apply_result()` uses `interaction.response.send_modal()` only on the undeferred path, encodes the Modal custom ID from the plugin result's action plus `inherited_route_token`, and uses `interaction.edit_original_response()`/followup on deferred paths.

- [ ] **Step 4: Register one adapter listener without overriding Discord internals**

In the adapter's existing client event setup, add an `on_interaction` listener that calls the bridge. Do not replace built-in View callbacks; return immediately for any custom ID not starting `hdi1.`.

- [ ] **Step 5: Run the new gateway tests and baseline component tests**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/gateway/test_discord_plugin_interactions.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py
```

Expected: all pass; existing clarify buttons still callback exactly once.

- [ ] **Step 6: Commit router task**

```bash
git add plugins/platforms/discord/plugin_interactions.py plugins/platforms/discord/adapter.py tests/gateway/test_discord_plugin_interactions.py
git commit -m "feat: route plugin Discord interactions with ACK guarantees"
```

---

### Task 6: Reload, revoke, and regression verification

**Files:**
- Modify: `tests/hermes_cli/test_discord_interactions.py`
- Modify: `tests/gateway/test_discord_plugin_interactions.py`
- No production file unless a failing test exposes a defect.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified core tranche with no quiz dependency.

- [ ] **Step 1: Add unload/reload and runtime revoke tests**

Load a fixture plugin, register a handler, dispose/unload, assert the manager accessor returns `None`, re-register a new handler, and assert only the new identity is invoked. For revoke, allow registration, flip the capability mock to false before dispatch/send/update, and assert all three fail closed without touching callback/adapter.

- [ ] **Step 2: Add forged namespace and stable restart routing test**

Construct a valid custom ID for `cs-quiz`, dispatch through a fresh bridge with a fresh fake Discord cache and the same manager registration, and assert it routes. Change plugin ID, route token, channel, and message fields independently; host rejects malformed namespace while the fixture plugin callback rejects valid-but-stale domain fields.

- [ ] **Step 3: Run all new and baseline tests**

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/hermes_cli/test_plugin_config_state_bridge.py \
  tests/hermes_cli/test_plugin_capabilities.py \
  tests/hermes_cli/test_discord_interactions.py \
  tests/gateway/test_discord_plugin_interactions.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py \
  tests/gateway/test_discord_platform_events.py
```

Expected: all pass. The three unchanged baseline Discord files must still report at least their baseline 41 passing tests; a lower collection count is failure even if pytest exits zero.

- [ ] **Step 4: Run diff and secret gates**

```bash
git diff --check
git status --short
git diff --stat
```

Run a staged credential scanner that prints only match count and file paths, never matching lines or values. Expected: 0 matches. Confirm production diff is limited to the four approved core modules plus tests and the RFC/plan docs.

- [ ] **Step 5: Commit final test hardening**

```bash
git add tests/hermes_cli/test_discord_interactions.py tests/gateway/test_discord_plugin_interactions.py
git commit -m "test: harden Discord plugin interaction lifecycle"
```

- [ ] **Step 6: Stop at tranche approval gate**

Report exact test counts, changed files, commit IDs, and unresolved risks. Do not begin `cs-quiz` plugin implementation until the core tranche is reviewed and approved. Do not push without explicit remote-write approval.
