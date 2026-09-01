# Core Task 5 report

## Status

**DONE**

Core Task 5B wires the reviewed Task 5A bridge into the real `DiscordAdapter` client lifecycle with one additive Discord `on_interaction` listener per newly created client. It preserves reviewed bridge behavior and existing Discord event/View dispatch.

## Implemented scope

- Registered the plugin listener with Discord client's supported `add_listener(callback, "on_interaction")` mechanism rather than `@client.event`, so an existing `on_interaction` event handler and Discord View callbacks are not replaced.
- Returned before bridge dispatch for custom IDs outside the reserved `hdi1.` namespace.
- Routed owned button and Modal submissions to the existing `DiscordPluginInteractionBridge.handle_interaction()` exactly once.
- Registered once on each newly created client. The existing reconnect path closes the old client, creates a fresh client, and gives each client one listener without accumulating callbacks on the replacement.
- Contained unexpected listener-boundary exceptions so they do not escape into the Discord gateway event loop. Each failure logs one fixed message containing one fresh lowercase 32-hex trace ID and no exception, interaction, custom-ID, route, user, component, or credential payload.
- Did not modify `DiscordPluginInteractionBridge`, its reviewed ACK/routing/auth/capability/profile semantics, renderer/send/update behavior, or any excluded domain/state/dependency/View-registry/deployment surface.

## Strict RED→GREEN evidence

1. **Exactly one `add_listener` registration:** RED failed because the connected fake real-lifecycle client had no `on_interaction` listeners (`[] != ["on_interaction"]`); GREEN passed after one additive registration.
2. **Non-owned coexistence:** RED failed because a `clarify.choice` interaction reached the bridge once; GREEN passed after the listener-level `hdi1.` guard, with the simulated existing View callback awaited exactly once and every response/ACK surface untouched.
3. **Owned button and Modal dispatch:** with the bridge call removed, both parameterized cases failed because the bridge was awaited zero times; restoring the minimal call made both pass exactly once.
4. **Reconnect/new client:** with duplicate registration introduced, RED observed `[2, 2]` listeners; the single registration made the two-client reconnect path GREEN with `[1, 1]`, old-client closure, and one bridge call from the replacement client.
5. **Exception containment/redaction:** RED propagated a private-marker `RuntimeError`; GREEN returned normally and logged exactly `discord_plugin_interaction_listener_failed trace=<32 lowercase hex>` once, with every independent private marker absent.

## Verification

### Listener-focused tests

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py -k "connect_registers or non_owned_interaction_coexists or owned_button_and_modal or reconnect_registers or listener_contains"
6 passed, 42 deselected in 1.01s
```

### Entire reviewed Task 5A bridge suite

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py -k "not connect_registers and not non_owned_interaction_coexists and not owned_button_and_modal and not reconnect_registers and not listener_contains"
42 passed, 6 deselected in 8.13s
```

### Combined bridge and listener file

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
48 passed in 8.69s
```

### Clarify/component/platform/auth regressions and Tasks 2–4 contracts

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_plugin_capabilities.py tests/hermes_cli/test_discord_interactions.py tests/gateway/test_discord_send.py tests/gateway/test_discord_clarify_buttons.py tests/gateway/test_discord_component_auth.py tests/gateway/test_discord_platform_events.py
214 passed in 92.24s (0:01:32)
```

This gate keeps active-profile manager/capability contracts, Task 4 send/update rendering, existing clarify/View callbacks, component authorization, and platform event behavior covered.

### Ruff

```text
uv run --no-project --python 3.11 --with ruff ruff check plugins/platforms/discord/adapter.py tests/gateway/test_discord_plugin_interactions.py
All checks passed!
```

### Diff and scope checks

```text
git diff --check
exit 0, no output

git diff --name-only
plugins/platforms/discord/adapter.py
tests/gateway/test_discord_plugin_interactions.py
```

The production diff is 14 additive lines at the real client creation/event-registration seam. `plugins/platforms/discord/plugin_interactions.py` remains byte-for-byte unchanged from reviewed head `da0aae76cc7d4c9e3a157c32133519d7dd271654`.

### Staged credential scan

`detect-secrets` scanned only the three approved staged adapter, test, and report paths. Its JSON output was reduced to match count and matching paths only; no matching lines or values were printed.

```text
matches=0
paths:
```

## Files changed

- Modified `plugins/platforms/discord/adapter.py`.
- Modified `tests/gateway/test_discord_plugin_interactions.py`.
- Created `.superpowers/sdd/2026-09-01-discord-interactions-core/task-5-report.md`.

## Self-review

- Scope is exactly the approved adapter, interaction test, and report files.
- Registration uses `add_listener`; no `on_interaction` event decorator or assignment can replace existing handlers.
- The registration statement executes once after each `commands.Bot` construction. Re-entry closes the old client before constructing the replacement, and no listener collection is copied between clients.
- The namespace guard reads only `interaction.data.custom_id`, coerces it safely to text, and performs no ACK, response, callback, or bridge work for non-owned IDs.
- Owned interactions make one direct await of the already-reviewed bridge; no bridge semantics or result handling were duplicated in the adapter.
- Exception isolation is intentionally only at the listener boundary. It logs no exception object (`exc_info` is absent), custom ID, interaction object, plugin/user IDs, or payload values; one UUID is generated and interpolated into one fixed log event.
- Tests exercise the real adapter `connect()` registration path with newly created clients, not only a detached helper, and prove additive coexistence, button/modal dispatch, replacement-client behavior, and redaction.
- No quiz-domain names, state, dependency, cross-platform abstraction, message-per-View restoration, deployment configuration, or unrelated refactor was added.

## Concerns

- The inherited Task 5A limitation remains unchanged: timing out an `asyncio.to_thread` plugin handler bounds host waiting but cannot terminate the worker or roll back side effects.
- The previously deferred minor bridge-test gaps recorded in the Task 5A reports remain outside Task 5B listener scope.

## Fix round 1/5 — listener namespace-inspection boundary

### Status

**DONE**

The Task 5B Important defect is fixed without addressing either deferred Minor or Task 6. The existing listener `try` now starts before `interaction.data` access and therefore covers `getattr`, mapping `.get`, `str(custom_id)`, the `hdi1.` guard, the normal nonowned early return, and the bridge await. The failure path remains one fixed trace-only log event.

### Strict RED→GREEN evidence

Focused RED, after adding `test_listener_contains_namespace_inspection_exception_with_one_trace` and before changing production:

```text
uv run pytest tests/gateway/test_discord_plugin_interactions.py::test_listener_contains_namespace_inspection_exception_with_one_trace -q
FAILED tests/gateway/test_discord_plugin_interactions.py::test_listener_contains_namespace_inspection_exception_with_one_trace
RuntimeError: PRIVATE_NAMESPACE_DATA
1 failed in 8.05s
```

The exception escaped from `plugins/platforms/discord/adapter.py:1389` at `getattr(interaction, "data", None)`, proving the test exercised the uncovered pre-bridge boundary.

Focused GREEN after only expanding the existing `try` boundary:

```text
uv run pytest tests/gateway/test_discord_plugin_interactions.py::test_listener_contains_namespace_inspection_exception_with_one_trace -q
1 passed in 2.81s
```

The test asserts normal listener return, no bridge await, exactly one fixed `discord_plugin_interaction_listener_failed trace=<32 lowercase hex>` message, and absence of the private namespace marker.

### Fresh verification

Listener-focused gate (the original six listener cases plus the new adversarial case):

```text
uv run pytest -q tests/gateway/test_discord_plugin_interactions.py -k "connect_registers or non_owned_interaction_coexists or owned_button_and_modal or reconnect_registers or listener_contains"
7 passed, 42 deselected in 1.44s
```

Complete pre-existing 48-test bridge/listener file plus the new regression:

```text
uv run pytest -q tests/gateway/test_discord_plugin_interactions.py
49 passed in 11.25s
```

Required regression gate:

```text
uv run pytest -q tests/hermes_cli/test_plugin_capabilities.py tests/hermes_cli/test_discord_interactions.py tests/gateway/test_discord_send.py tests/gateway/test_discord_clarify_buttons.py tests/gateway/test_discord_component_auth.py tests/gateway/test_discord_platform_events.py
214 passed in 74.42s (0:01:14)
```

Targeted Ruff:

```text
uv run ruff check plugins/platforms/discord/adapter.py tests/gateway/test_discord_plugin_interactions.py
All checks passed!
```

Diff/scope checks before report append:

```text
git diff --check
exit 0, no output

git diff --name-only
plugins/platforms/discord/adapter.py
tests/gateway/test_discord_plugin_interactions.py
```

Final staged credential scan covered only the approved adapter, test, and report paths and printed count/paths only:

```text
matches=0
paths:
```

### Files and self-review

- Modified `plugins/platforms/discord/adapter.py`: moved only the existing `try` start; logging and bridge semantics are unchanged.
- Modified `tests/gateway/test_discord_plugin_interactions.py`: added one adversarial `interaction.data` property failure regression.
- Appended this fix evidence to `.superpowers/sdd/2026-09-01-discord-interactions-core/task-5-report.md`.
- Normal nonowned `hdi1.` guard behavior remains an early return inside the protected boundary; existing coexistence coverage passes.
- No Task 5A bridge implementation, deferred Minor, Task 6, domain, dependency, deployment, history, or secret surface changed.

### Concerns

- No new concern. The inherited Task 5A `asyncio.to_thread` timeout limitation and previously deferred Minor test gaps remain unchanged and outside this fix round.
