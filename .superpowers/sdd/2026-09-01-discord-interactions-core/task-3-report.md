# Core Task 3 implementation report

## Status

**DONE**

Core Task 3 is implemented with strict vertical RED→GREEN evidence. Task 2+3 contracts, relevant `PluginContext` regressions, Ruff, and diff checks are green.

## Scope completed

- Added the narrow `DiscordInteractions(plugin_id)` facade with `send()` and `update()`.
- Rechecks `gateway.discord_interactions` at every call and fails closed when consent reads fail.
- Resolves the live gateway runner and only its `Platform.DISCORD` adapter at call time.
- Returns stable envelopes for missing runner, missing/disconnected adapter, invalid IDs/specs, malformed receipts, and adapter exceptions.
- Validates IDs as non-empty ASCII decimal strings and validates/copies specs through Task 2 `validate_message_spec()` without mutating caller input.
- Calls only `plugin_interaction_send` and `plugin_interaction_update`.
- Normalizes success/failure receipts, strips adapter extras, and never returns raw SDK objects or exception text.
- Logs unexpected adapter failures with a generated 32-hex trace ID while omitting exception text from both the plugin envelope and host log message.
- Added lazy cached `PluginContext.discord`, bound to canonical `self.plugin_id`.
- Did not modify the Discord adapter, renderer, Task 2 codec/validators/capability/registration, `PlatformActions`, quiz domain, or cross-platform seams.

## RED/GREEN evidence

### Slice 1: send success receipt and runtime capability revoke

RED, after adding the two focused tests before the facade existed:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py::test_send_rechecks_capability_and_returns_normalized_receipt tests/hermes_cli/test_discord_interactions.py::test_update_revocation_fails_before_adapter
ImportError: cannot import name 'DiscordInteractions' from 'hermes_cli.discord_interactions'
1 error in 3.67s (exit 4)
```

GREEN, after the smallest send/update facade:

```text
..                                                                       [100%]
2 passed in 4.85s
```

### Slice 2: consent read failure, gateway unavailable, adapter absent/disconnected

RED:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py -k 'consent_read_failure or gateway_unavailable or adapter_absent_or_disconnected'
FFFF                                                                     [100%]
4 failed, 76 deselected in 3.70s
```

Expected failures were an escaping consent `OSError`, runner `None` `AttributeError`, absent adapter `KeyError`, and disconnected adapter still reaching a missing send method.

GREEN:

```text
....                                                                     [100%]
4 passed, 76 deselected in 13.81s
```

### Slice 3: invalid channel/message IDs and invalid message specs

RED:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py -k 'invalid_channel_id or invalid_ids or invalid_spec_without_raising'
FFFFFFFFFFFFFF                                                           [100%]
14 failed, 80 deselected in 1.82s
```

Expected failures showed invalid IDs reaching the adapter and invalid specs raising `ValueError` rather than returning `invalid_argument`.

GREEN:

```text
..............                                                           [100%]
14 passed, 80 deselected in 23.89s
```

### Slice 4: update success, normalized adapter failures, malformed receipts

RED:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py -k 'normalizes_success or failure_receipt or malformed_adapter_receipt'
.F.FFFFFFFFFFF                                                           [100%]
12 failed, 2 passed, 94 deselected in 27.52s
```

Expected failures were raw `TypeError`/`KeyError`, false send receipts treated as success, and invalid receipt fields trusted unchanged.

GREEN:

```text
..............                                                           [100%]
14 passed, 94 deselected in 22.63s
```

### Slice 5: adapter exceptions and non-secret trace IDs

RED:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py -k 'adapter_exception_returns'
FF                                                                       [100%]
2 failed, 108 deselected in 21.45s
```

Both send and update leaked the adapter exception by raising it into the caller.

GREEN:

```text
..                                                                       [100%]
2 passed, 108 deselected in 1.95s
```

The tests assert the returned envelope is exactly `{"ok": false, "error_code": "adapter_error"}`, exception text is absent from the result and captured log, and the host log contains `trace_id=<32 lowercase hex>`.

### Slice 6: `PluginContext.discord` cache and canonical binding

RED:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py::test_plugin_context_discord_is_cached_and_bound_to_canonical_id
AttributeError: 'PluginContext' object has no attribute 'discord'
1 failed in 7.95s
```

GREEN:

```text
.                                                                        [100%]
1 passed in 1.40s
```

## Final verification

### Task 2+3 contract tests

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_discord_interactions.py tests/hermes_cli/test_plugin_capabilities.py
........................................................................ [ 48%]
........................................................................ [ 96%]
......                                                                   [100%]
150 passed in 13.34s
```

### Relevant `PluginContext` and lifecycle regressions

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_platform_actions.py::TestPluginContextWiring
.                                                                        [100%]
1 passed in 2.89s
```

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_plugins.py -k 'force_reload or unload_all'
..                                                                       [100%]
2 passed, 63 deselected in 1.43s
```

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_plugin_ownership_ledger.py::test_load_force_reload_and_unload_remove_every_manager_registration tests/hermes_cli/test_plugin_ownership_ledger.py::test_manager_local_override_does_not_resurrect_after_targeted_unload tests/hermes_cli/test_plugin_ownership_ledger.py::test_spawned_supervised_task_is_cancelled_on_unload tests/hermes_cli/test_plugin_ownership_ledger.py::test_on_unload_exception_does_not_block_other_teardown
....                                                                     [100%]
4 passed in 4.80s
```

### Ruff and diff checks

```text
uv run --no-project --python 3.11 --with ruff ruff check hermes_cli/discord_interactions.py hermes_cli/plugins.py tests/hermes_cli/test_discord_interactions.py
All checks passed!
```

```text
git diff --check
(exit 0, no output)
```

## Files created or modified

- Modified `hermes_cli/discord_interactions.py`.
- Modified `hermes_cli/plugins.py`.
- Extended `tests/hermes_cli/test_discord_interactions.py`.
- Created this report.

## Self-review

- Facade instances retain only the canonical plugin ID; runner and adapter are resolved at call time.
- Capability is checked before every send/update call and capability read exceptions fail closed.
- IDs use strict ASCII decimal validation; specs are copied by the preserved Task 2 validator.
- Only the two approved adapter entry points are invoked.
- Success and failure receipts are reconstructed into host-owned dictionaries; adapter extras and unsupported error codes are not exposed.
- Unexpected adapter exception text is not returned or logged; a random trace ID is logged for correlation.
- `PluginContext.discord` is lazy and returns the same object for repeated access.
- Diff is restricted to approved Task 3 implementation, tests, and report; no dependency, adapter, renderer, quiz, or shared abstraction changes were made.

## Concerns

None.
