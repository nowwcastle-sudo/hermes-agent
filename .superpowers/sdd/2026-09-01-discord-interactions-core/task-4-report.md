# Core Task 4 implementation report

## Status

**DONE**

Core Task 4 is complete. The inherited renderer and cache-independent send/update implementation was inspected against the takeover handoff, Task 4 brief, reviewer constraints, and binding RFC. It was preserved because no concrete compliance defect was found. The final takeover slice added the missing log-redaction regression first, demonstrated the intended send/update RED, and replaced exception logging with operation/plugin/trace-only logging while preserving exact receipts and error mappings.

## Scope completed

- Added `DiscordPluginInteractionBridge` using native `discord.Embed`, `discord.ui.View(timeout=None)`, and `discord.ui.Button`.
- Re-validates message specs and maps only approved content, embed, field, and button fields.
- Encodes custom IDs from the canonical facade-supplied `plugin_id`, action, and opaque route token.
- Added cache-hit and REST cache-miss send behavior with exact send receipts.
- Added cache-independent channel/message fetch and exact update receipts.
- Preserved `invalid_argument`, send `channel_not_found`, update `message_not_found`, `forbidden`, and generic `discord_error` mappings.
- Added the missing captured-log assertion proving generic `RuntimeError("private SDK detail")` is not logged for either send or update.
- Generic failures now generate a random 32-hex UUID trace and log only operation, canonical plugin ID, and trace. They do not log the exception object, traceback, spec, route token, or secret values.
- Did not add Task 5 listener/dispatch, ACK/defer, authorization, handler lookup, View restoration, or Modal routing.
- Added no dependency and performed no broad adapter refactor.

## Inherited controller-verified evidence

This evidence predates the final takeover slice and is recorded separately rather than represented as rerun evidence:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
13 passed in 2.49s

git diff --check
exit 0
```

At takeover, `HEAD` was `5e63ac8123432b705314321ef99c9c927ef35826`. The controller identified only these intentional Task 4 changes:

```text
M plugins/platforms/discord/adapter.py
?? plugins/platforms/discord/plugin_interactions.py
?? tests/gateway/test_discord_plugin_interactions.py
```

The inherited implementation covered rendering, canonical namespace encoding, cache-hit/cache-miss send, cache-independent update, exact success/error envelopes, and real facade-to-adapter integration. Inspection found no concrete defect outside the known logging leak, so the implementation was not rewritten.

## Final takeover slice RED/GREEN evidence

### RED — captured log redaction

The assertion `"private SDK detail" not in caplog.text` was added before production logging changed.

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py::test_discord_failures_return_exact_safe_envelopes
..F..F
2 failed, 4 passed in 0.95s
```

Both expected failures were the generic `discord_error` cases:

```text
send-raised2-discord_error
update-raised5-discord_error
```

The four NotFound/Forbidden mappings remained green. Both failures showed `RuntimeError: private SDK detail` leaked through `logger.exception` into `caplog.text`.

### GREEN — operation/plugin/trace-only logging

The smallest production change imported stdlib `uuid` and replaced each `logger.exception` call with `logger.error` arguments containing only operation, plugin ID, and `uuid.uuid4().hex`.

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py::test_discord_failures_return_exact_safe_envelopes
6 passed in 2.03s
```

Exact return envelopes and all NotFound/Forbidden mappings were unchanged.

## Final verification

### Task 4 tests

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
13 passed in 2.05s
```

### Combined Tasks 2–4 contracts and relevant Discord send/component regressions

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/hermes_cli/test_plugin_capabilities.py \
  tests/hermes_cli/test_discord_interactions.py \
  tests/gateway/test_discord_plugin_interactions.py \
  tests/gateway/test_discord_send.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py \
  tests/gateway/test_discord_platform_events.py
221 passed in 14.32s
```

### Ruff

```text
uv run --no-project --python 3.11 --with ruff ruff check \
  plugins/platforms/discord/adapter.py \
  plugins/platforms/discord/plugin_interactions.py \
  tests/gateway/test_discord_plugin_interactions.py
All checks passed!
```

### Diff check

```text
git diff --check
exit 0, no output
```

### Staged credential scan

`detect-secrets` scanned the staged approved code/test blobs. Its JSON output was captured and reduced to count and paths only; no matching lines or values were printed.

```text
matches=0
paths:
```

A final `detect-secrets` scan of all four staged approved files, including this report, produced the same result:

```text
matches=0
paths:
```

## Files created or modified

- Modified `plugins/platforms/discord/adapter.py`.
- Created `plugins/platforms/discord/plugin_interactions.py`.
- Created `tests/gateway/test_discord_plugin_interactions.py`.
- Created this report at `.superpowers/sdd/2026-09-01-discord-interactions-core/task-4-report.md`.

## Self-review

- The real `DiscordAdapter` is used; no nonexistent plan-class substitute was introduced.
- Corrected internal signatures remain plugin-ID-first while public `ctx.discord.send/update` are unchanged.
- Namespace comes only from canonical facade state; message specs cannot supply or override custom IDs.
- Rendering uses only approved native Discord fields and does not expose answer/rubric/unknown fields.
- Route tokens and plugin IDs are not rendered as visible content.
- Cache miss uses REST channel fetch; update always fetches the message by numeric ID.
- Success and failure dictionaries are reconstructed exactly and contain no SDK objects or exception text.
- Generic logging does not pass `exc_info`, the exception object, full spec, route token, or credentials.
- UUID4 hex trace IDs are non-secret correlation identifiers.
- NotFound and Forbidden branches precede the generic exception branch and retain their stable mappings.
- Diff is limited to the three approved Task 4 implementation/test files plus this report.
- No Task 5 behavior, dependency, quiz-domain code, cross-platform abstraction, or unrelated cleanup is present.

## Concerns

None.

## Fix Round 1 — component value serialization

### Status

**DONE**

The binding Important finding was reproduced and fixed without addressing deferred Minors or Task 5. Button `value` is now routing data carried in the host-owned custom ID rather than an arbitrary Python attribute that discord.py omits from serialization.

### Scope

- Extended `encode_custom_id` backward-compatibly to emit `hdi1.<plugin_b64>.<action>.<route_token>[.<value_b64>]`.
- Preserved the exact four-segment encoding and decode result when no value is supplied.
- Encoded present non-empty UTF-8 values with canonical unpadded URL-safe base64 and retained the total 90-character budget.
- Rejected empty, padded/non-canonical, malformed, and non-UTF-8 fifth segments before routing.
- Exposed a decoded fifth segment as `component_value`.
- Passed the validated component value into the codec from the Discord renderer and removed the arbitrary `button.value` assignment.
- Added a native discord.py 2.7.1 `Button.to_component_dict()` plus `View.to_components()` smoke test proving serialized `custom_id` retains and recovers `component_value`.
- Updated the binding RFC wire contract and recovery/security description.

### RED evidence

Tests were added before production changes. The focused command used real discord.py 2.7.1 for the serialization smoke:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio --with discord.py==2.7.1 pytest -q tests/hermes_cli/test_discord_interactions.py -k 'custom_id' tests/hermes_cli/test_discord_component_serialization.py
3 failed, 23 passed, 98 deselected, 1 warning in 4.34s
```

The failures were the expected missing behavior: both four-argument codec calls raised `TypeError`, and native serialized decode lacked `component_value` (`KeyError`).

### Focused GREEN evidence

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio --with discord.py==2.7.1 pytest -q tests/hermes_cli/test_discord_interactions.py -k 'custom_id' tests/hermes_cli/test_discord_component_serialization.py
26 passed, 98 deselected, 1 warning in 7.66s

uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
13 passed in 1.69s
```

The warning is discord.py 2.7.1 importing Python's deprecated `audioop` module; it is third-party noise unrelated to this change.

### Full Tasks 2–4 and Discord regression evidence

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_plugin_capabilities.py tests/hermes_cli/test_discord_interactions.py tests/gateway/test_discord_plugin_interactions.py tests/gateway/test_discord_send.py tests/gateway/test_discord_clarify_buttons.py tests/gateway/test_discord_component_auth.py tests/gateway/test_discord_platform_events.py
227 passed in 17.56s
```

### Ruff and diff evidence

```text
uv run --no-project --python 3.11 --with ruff ruff check hermes_cli/discord_interactions.py plugins/platforms/discord/plugin_interactions.py tests/hermes_cli/test_discord_interactions.py tests/hermes_cli/test_discord_component_serialization.py tests/gateway/test_discord_plugin_interactions.py
All checks passed!

git diff --check
exit 0, no output
```

### Files in Fix Round 1

- Modified `hermes_cli/discord_interactions.py`.
- Modified `plugins/platforms/discord/plugin_interactions.py`.
- Modified `tests/hermes_cli/test_discord_interactions.py`.
- Created `tests/hermes_cli/test_discord_component_serialization.py`.
- Modified `tests/gateway/test_discord_plugin_interactions.py`.
- Modified `docs/rfcs/2026-09-discord-plugin-interactions-cs-quiz.md`.
- Appended this Fix Round 1 evidence to `task-4-report.md`.

### Staged credential scan

`detect-secrets` scanned all seven staged approved code, test, RFC, and report files. Its JSON output was reduced to count and paths only; no matching lines or values were printed.

```text
matches=0
paths=
```

### Fix Round 1 concerns

None. The native smoke emits only the documented third-party `audioop` deprecation warning.
