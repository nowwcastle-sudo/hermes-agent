# Core Task 5A-1 takeover report

## Status

**DONE**

The inherited Core Task 5A-1 bridge slices were inspected and frozen without behavior changes. The current diff is limited to the approved bridge and gateway test files, implements only the reviewed 5A-1 boundary, and leaves deferred Modal/error/payload completion to Task 5A-2 and listener wiring to Task 5B.

## Scope frozen

- Non-`hdi1.` custom IDs return `False` without ACK or callback.
- Owned malformed IDs receive one bounded ephemeral ACK.
- Host component authorization runs before callback; denial is acknowledged once.
- Active-profile handler lookup and runtime capability recheck fail closed.
- Generic `open_*` buttons remain undeferred and route valid `open_modal` results.
- Returned Modal custom IDs use the canonical plugin ID and inherit the original route token; validated field IDs and limits are preserved.
- Optional `component_value` is propagated into the callback payload.
- Non-open buttons defer before callback and apply valid message updates.
- No quiz action names, listener registration, adapter changes, dependency changes, cross-platform abstraction, or Task 5A-2 behavior were added.

## Inherited controller-verified evidence

The following evidence predates this takeover and is recorded separately; it is not represented as final verification performed here:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
21 passed in 1.27s

git diff --check
exit 0
```

At takeover, the controller identified exactly these intentional uncommitted files:

```text
M plugins/platforms/discord/plugin_interactions.py
M tests/gateway/test_discord_plugin_interactions.py
```

## Final verification performed during takeover

### Focused Core Task 5A-1 gateway suite

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
21 passed in 1.22s
```

### Task 4 gateway regressions

The seven pre-5A gateway test definitions, including parametrized cases, were selected explicitly:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q <seven Task 4 gateway node IDs>
13 passed in 3.23s
```

### Tasks 2–4 contracts and relevant Discord auth/clarify/platform regressions

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/hermes_cli/test_plugin_capabilities.py \
  tests/hermes_cli/test_discord_interactions.py \
  tests/gateway/test_discord_send.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py \
  tests/gateway/test_discord_platform_events.py
214 passed in 11.65s
```

### Ruff

```text
uv run --no-project --python 3.11 --with ruff ruff check \
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

`detect-secrets` scanned only the three staged approved bridge, test, and report paths. Its JSON output was reduced to count and paths; no matching lines or values were printed.

```text
matches=0
paths:
```

## Files frozen

- Modified `plugins/platforms/discord/plugin_interactions.py`.
- Modified `tests/gateway/test_discord_plugin_interactions.py`.
- Created this report at `.superpowers/sdd/2026-09-01-discord-interactions-core/task-5a1-report.md`.

## Self-review

- Production routing is domain-agnostic: only the reserved generic `open_` prefix selects the undeferred path.
- Non-owned interactions are untouched; malformed/unauthorized/unavailable owned interactions stop before callback.
- Handler lookup comes from the active-profile manager and capability is checked at runtime.
- The Modal namespace and route token are host-controlled; component values are decoded routing data rather than visible or arbitrary SDK attributes.
- Deferred non-open buttons acknowledge before invoking the handler and reuse the Task 4 renderer for updates.
- The diff contains no listener registration, `DiscordAdapter` modification, quiz-domain state/action names, View restoration, dependency, or unrelated cleanup.
- Synchronous handlers, modal-submit normalization, unknown-interaction ACK failures, timeout/error handling, trace IDs, and the full result matrix are intentionally absent because they are assigned to Task 5A-2.

## Concerns

None. The outstanding behavior listed in the final self-review bullet is explicitly assigned to Task 5A-2, not a defect in the frozen 5A-1 slice.
