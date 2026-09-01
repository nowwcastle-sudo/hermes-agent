# Core Task 5A-2a report

## Status

**DONE**

Core Task 5A-2a extends reviewed 5A-1 with modal-submit normalization, exact serializable callback payload coverage, deferred `open_modal` rejection, and unknown-interaction defer stop behavior. It does not add handler threading, timeout/error/result-matrix completion, listener wiring, adapter changes, domain behavior, or dependencies.

## Implemented scope

- Detect Discord modal submissions from `interaction.type` and defer exactly once before callback.
- Flatten nested modal action rows into `modal_values`, accepting only dictionary components with string `custom_id` and string `value`.
- Ignore malformed rows/components and SDK objects deterministically; duplicate valid IDs resolve by input order with the last valid value winning.
- Preserve exact v1 JSON payload fields for button and modal-submit callbacks, including optional `component_value` and modal `modal_values`.
- Reject `open_modal` returned by a deferred modal submit with one bounded ephemeral follow-up and no second initial ACK.
- Treat Discord SDK `NotFound` or HTTP code `10062` during initial defer as an owned expired interaction: stop before callback, do not retry, and touch no other response surface.

## RED to GREEN evidence

### Modal-submit defer and nested values

RED:

```text
pytest ...::test_modal_submit_defers_before_handler_with_nested_text_values
FAILED: callback payload had kind='button' and modal_values={}
```

GREEN after adding modal-submit detection and nested string extraction:

```text
1 passed in 1.10s
```

### Malformed nested modal components

RED:

```text
pytest ...::test_modal_submit_ignores_malformed_nested_components
FAILED: TypeError: 'NoneType' object is not iterable
```

GREEN after adding list/dict shape guards:

```text
2 passed in 1.01s
```

The two-test GREEN run covered both nested valid values and malformed nested structures.

### Exact JSON payloads

The exact button/modal payload characterization passed on its first focused run after the preceding modal slice because reviewed 5A-1 already supplied the exact button fields and the modal slice supplied the modal-specific fields:

```text
pytest ...::test_button_and_modal_payloads_have_exact_json_v1_fields
1 passed in 1.16s
```

The test compares complete dictionaries (therefore exact key sets and values) and proves `json.dumps(payload)` succeeds for both kinds.

### Deferred modal `open_modal` rejection

RED:

```text
pytest ...::test_deferred_modal_submit_rejects_open_modal_without_second_ack
FAILED: ValueError: open_modal is only valid for a button interaction
```

GREEN after adding the narrow post-defer rejection:

```text
1 passed in 1.01s
```

The test proves one defer, one bounded ephemeral follow-up, and no `send_message`, `send_modal`, or edit.

### Unknown-interaction defer stop

RED:

```text
pytest ...::test_unknown_interaction_defer_failure_stops_before_callback
FAILED: UnknownInteraction(code=10062) escaped from response.defer()
```

The first implementation exposed that this test environment provides `discord.NotFound` as a mock rather than a class; the SDK type check was guarded without broadening swallowed exceptions. Final GREEN:

```text
1 passed in 0.81s
```

The test proves one defer attempt, no retry, no callback, and no other response/follow-up/edit call.

## Final verification

### Focused 5A-2a tests

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/gateway/test_discord_plugin_interactions.py::test_modal_submit_defers_before_handler_with_nested_text_values \
  tests/gateway/test_discord_plugin_interactions.py::test_modal_submit_ignores_malformed_nested_components \
  tests/gateway/test_discord_plugin_interactions.py::test_button_and_modal_payloads_have_exact_json_v1_fields \
  tests/gateway/test_discord_plugin_interactions.py::test_deferred_modal_submit_rejects_open_modal_without_second_ack \
  tests/gateway/test_discord_plugin_interactions.py::test_unknown_interaction_defer_failure_stops_before_callback
5 passed in 1.86s
```

### Entire gateway bridge suite

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
26 passed in 1.51s
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
214 passed in 61.83s
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

## Files changed

- Modified `plugins/platforms/discord/plugin_interactions.py`.
- Modified `tests/gateway/test_discord_plugin_interactions.py`.
- Created `.superpowers/sdd/2026-09-01-discord-interactions-core/task-5a2a-report.md`.

## Self-review

- Scope is limited to the approved bridge, test, and report files.
- Production remains domain-agnostic; no quiz action names or state were introduced.
- Reviewed 5A-1 non-owned, malformed, auth, capability, undeferred generic `open_`, inherited Modal routing, component-value, and deferred update behavior remains covered by the complete bridge suite.
- Modal normalization emits only strings in a plain dictionary and never passes nested SDK objects through.
- Payload tests assert complete dictionaries rather than subsets and serialize both button and modal payloads.
- Unknown-interaction handling is narrow: SDK `NotFound` or code `10062` stops; unrelated exceptions still propagate for 5A-2b handling.
- Deferred modal `open_modal` uses only a post-defer follow-up and cannot issue a second initial ACK.
- No `asyncio.to_thread`, new timeout constants, general exception/trace handling, complete ephemeral/no-change matrix, listener registration, `DiscordAdapter` change, dependency, View restoration, or unrelated cleanup was added; those remain assigned to 5A-2b/5B.

## Concerns

None within 5A-2a scope. General handler execution/error/result semantics remain intentionally incomplete until Core Task 5A-2b.

## Fix Round 1 — 5A-2a Important payload finding

### Scope

Fixed only the reviewed Important per-kind payload violation. The exact payload test now uses an adversarial modal-submit custom ID with a fifth decoded `component_value` segment while preserving nested `modal_values`; the bridge emits `component_value` only when the normalized payload kind is `button`. Deferred Minors and 5A-2b/5B remain untouched.

### Strict RED to GREEN evidence

RED after changing only the exact payload test:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py::test_button_and_modal_payloads_have_exact_json_v1_fields
FAILED tests/gateway/test_discord_plugin_interactions.py::test_button_and_modal_payloads_have_exact_json_v1_fields
At index 1 diff: modal payload unexpectedly contained 'component_value': 'injected-button-value'
1 failed in 1.18s
```

GREEN after the minimal `payload["kind"] == "button"` guard:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py::test_button_and_modal_payloads_have_exact_json_v1_fields
1 passed in 3.17s
```

The complete-dictionary assertion proves the button retains `component_value`, the adversarial modal excludes it, and modal `modal_values` remains exactly `{"answer": "two"}`. The existing `json.dumps` assertion still covers both payloads.

### Regression verification

Entire bridge tests:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/gateway/test_discord_plugin_interactions.py
26 passed in 12.51s
```

Tasks 2–4/auth/clarify regressions:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/hermes_cli/test_plugin_capabilities.py tests/hermes_cli/test_discord_interactions.py tests/gateway/test_discord_send.py tests/gateway/test_discord_clarify_buttons.py tests/gateway/test_discord_component_auth.py tests/gateway/test_discord_platform_events.py
214 passed in 64.77s
```

Ruff:

```text
uv run --no-project --python 3.11 --with ruff ruff check plugins/platforms/discord/plugin_interactions.py tests/gateway/test_discord_plugin_interactions.py
All checks passed!
```

Diff check before staging:

```text
git diff --check
exit 0, no output
```

Staged credential scan over only the three approved bridge, test, and report paths (JSON reduced to count and matching paths; no values printed):

```text
matches=0
paths:
```
