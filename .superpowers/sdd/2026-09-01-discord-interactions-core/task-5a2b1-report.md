# Core Task 5A-2b1 report

## Status

**DONE**

Core Task 5A-2b1 adds exact-once synchronous/coroutine/returned-awaitable handler execution and complete 2-second/90-second timeout selection to the reviewed 5A bridge. Timeout handling is intentionally narrow and temporary; the final trace-safe error/result matrix remains assigned to 5A-2b2, and listener wiring remains assigned to 5B.

## Implemented scope

- Coroutine functions are awaited directly and invoked once.
- Synchronous handlers run through `asyncio.to_thread` and are invoked once.
- An awaitable returned by a synchronous handler is awaited without re-invoking the handler.
- The complete invocation path is wrapped in `asyncio.wait_for`.
- Generic `open_*` buttons select `_OPEN_HANDLER_TIMEOUT_SECONDS = 2.0`.
- Every deferred button/modal path selects `_DEFERRED_HANDLER_TIMEOUT_SECONDS = 90.0`.
- Blocking synchronous and pending asynchronous handlers route `TimeoutError` through one internal timeout helper.
- An undeferred timeout sends one bounded initial ephemeral ACK; a deferred timeout sends one bounded ephemeral follow-up after the sole defer ACK.
- Existing 5A-1/5A-2a routing, auth, capability, payload, Modal, unknown-interaction, and render behavior remains unchanged.

## RED to GREEN evidence

### Synchronous handler via `asyncio.to_thread`

RED after adding the sync exact-once test:

```text
test_sync_handler_runs_in_thread_and_is_invoked_once
FAILED: TypeError: An asyncio.Future, a coroutine or an awaitable is required
1 failed
```

GREEN after adding the minimal invocation helper:

```text
1 passed in 3.22s
```

### Coroutine handler direct await

This is preserved reviewed behavior rather than a new production behavior. The focused characterization proved one direct invocation and no `asyncio.to_thread` call:

```text
test_coroutine_handler_is_awaited_directly_and_invoked_once
1 passed in 1.46s
```

### Awaitable returned by a synchronous handler

RED after adding the returned-awaitable test:

```text
test_sync_handler_returned_awaitable_is_awaited_without_reinvocation
FAILED: ValueError: Invalid Discord interaction result
RuntimeWarning: coroutine ... was never awaited
1 failed
```

GREEN after awaiting the single returned value when it is awaitable:

```text
1 passed in 0.90s
```

### Patchable timeout selection

RED after adding the 2-second/90-second selection test:

```text
test_handler_wait_for_selects_open_and_deferred_timeout_constants
FAILED: AttributeError: module ... has no attribute '_OPEN_HANDLER_TIMEOUT_SECONDS'
2 failed
```

GREEN after adding the two constants and selecting them at the single `wait_for` call:

```text
2 passed in 0.81s
```

The test patches both constants to 11/22 milliseconds and records the exact `wait_for` timeout without sleeping production durations.

### Blocking sync timeout and ACK ordering

RED:

```text
test_blocking_sync_open_handler_times_out_after_start_with_one_ack
FAILED: TimeoutError
1 failed
```

GREEN after adding the narrow undeferred timeout response:

```text
1 passed in 4.45s
```

The test uses a `threading.Event`, proves the handler starts once, proves no defer occurs, and proves the timeout initial ephemeral ACK follows handler start.

### Pending async timeout and shared timeout path

RED:

```text
test_pending_async_deferred_handler_times_out_after_defer_with_one_error
FAILED: TimeoutError
1 failed
```

GREEN after routing both timeout modes through `_handle_handler_timeout`:

```text
2 passed in 1.71s
```

The two-test GREEN run covers both timeout modes. The pending-async test uses a Future, proves ordering `defer -> handler_started -> timeout_error`, proves cancellation, and proves one defer plus one ephemeral follow-up with no second initial ACK.

## Final verification

### Focused invocation/timeout tests

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q <six focused node IDs>
7 passed in 5.72s
```

### Entire gateway bridge suite

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
33 passed in 4.02s
```

### Tasks 2–4 contracts and auth/clarify/platform regressions

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/hermes_cli/test_plugin_capabilities.py \
  tests/hermes_cli/test_discord_interactions.py \
  tests/gateway/test_discord_send.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py \
  tests/gateway/test_discord_platform_events.py
214 passed in 72.26s
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

`detect-secrets` scanned only the three staged approved bridge, test, and report paths. Its JSON output was reduced to count and matching paths; no secret values were printed.

```text
matches=0
paths:
```

## Files changed

- Modified `plugins/platforms/discord/plugin_interactions.py`.
- Modified `tests/gateway/test_discord_plugin_interactions.py`.
- Created `.superpowers/sdd/2026-09-01-discord-interactions-core/task-5a2b1-report.md`.

## Self-review

- The production diff is domain-agnostic and introduces no quiz action name, state, dependency, cross-platform abstraction, adapter/listener wiring, or deployment behavior.
- Handler invocation is exact-once: coroutine functions execute once directly; sync functions execute once in `to_thread`; only the returned object is subsequently awaited when needed.
- `wait_for` covers the whole invocation helper, including sync thread dispatch and any awaitable returned from the sync call.
- Timeout constants retain the reviewed production values and are patchable in millisecond tests.
- Deferred ACK happens before handler execution. Undeferred `open_*` does not defer and receives one initial ephemeral response only on timeout.
- Both timeout modes use the same internal timeout helper and introduce no raw exception text.
- A running worker thread cannot be forcibly terminated by `asyncio.wait_for`; the timeout bounds bridge waiting/response latency, while the test releases its blocking worker deterministically. This is inherent to the explicitly required `asyncio.to_thread` design.
- Final trace IDs, logging, exception/validator handling, and the complete result matrix are intentionally absent because they belong to 5A-2b2.

## Concerns

None within 5A-2b1 scope. The non-cancellable nature of an already-running `asyncio.to_thread` worker is an acknowledged Python runtime property, not an unimplemented requirement in this approved slice.
