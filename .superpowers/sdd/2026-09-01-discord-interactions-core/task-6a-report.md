# Core Task 6A report — unload/reload identity and runtime revoke

## Status

**DONE_WITH_CONCERNS**

Task 6A lifecycle and runtime-permission contracts are implemented and verified. One production defect was proven by a strict RED: a consent backend exception escaped the Discord bridge capability gate after an earlier successful dispatch. The bridge now normalizes that read failure to a denied capability and fails closed before callback invocation.

The only concern is the explicitly inherited three-failure ownership-ledger baseline. The Task 6A-focused ownership/unload regressions pass and Task 6A does not alter those unrelated registries.

## Scope completed

- Added handler identity coverage for both direct registration disposal and targeted plugin unload.
- Proved the manager accessor returns `None` after release, then a handler B registered under the same canonical plugin ID is the only handler invoked by the bridge; handler A is never resurrected.
- Added full manager unload/re-registration coverage through the real additive Discord listener path: the handler map clears, one fresh handler is registered, one listener exists, and the fresh handler is invoked once.
- Added runtime revoke and consent-read-exception coverage using an already-created registration, handler, bridge, stable custom ID, cached `DiscordInteractions` facade, and adapter.
- Proved a granted dispatch/send succeeds, then the same objects recheck the changed capability; bridge callback count remains one and facade send/update stop before another adapter await.
- Preserved manager-local registration, active registration identity, exact-one listener/ACK behavior, and the core/domain boundary.

## Strict RED → GREEN evidence

### Existing lifecycle behavior

The new disposal, targeted-unload, re-registration identity, and full-unload/listener cases passed on their first focused executions. This showed the reviewed lifecycle behavior already satisfied those new contract tests, so no lifecycle production change was made.

```text
test_released_handler_identity_is_not_resurrected_after_reregistration
2 passed

test_full_unload_reload_routes_listener_once_to_one_fresh_handler
1 passed
```

### Runtime revoke and consent exception

The runtime-revoke parameter passed before production changes. The consent-read-exception parameter failed for the intended reason after one successful dispatch:

```text
FAILED ...[consent-read-exception]
OSError: PRIVATE_CONSENT_BACKEND
plugins/platforms/discord/plugin_interactions.py:63
1 failed, 1 passed
```

The exception escaped from `plugin_capability_granted(...)`, proving the bridge did not fail closed on a consent backend read error.

The minimal production change catches only exceptions from that dispatch-time capability read, treats them as denial, and preserves the existing unavailable-feature ACK path. Focused GREEN:

```text
test_existing_bridge_and_registration_fail_closed_after_runtime_revoke
2 passed
```

The existing-facade contract also passed for both a boolean revoke and a consent-read exception:

```text
test_existing_registration_and_facade_recheck_runtime_capability_before_adapter
2 passed
```

### Combined-suite listener log isolation

The fresh combined contract/bridge invocation exposed two pre-existing order-dependent test failures: each listener redaction test saw the successful adapter connection INFO record plus the intended listener ERROR and therefore observed two `caplog.messages` entries. The tests now clear already-completed connection logs immediately before listener dispatch. This preserves the exact-one listener error assertion and made the combined gate GREEN without changing production logging.

## Verification

### Task 6A focused tests

```text
Gateway lifecycle/revoke focus: 5 passed, 49 deselected
Facade and existing unload focus: 4 passed, 121 deselected
```

The second count includes the two existing targeted/full-unload contract cases alongside the two new facade capability parameters. Across the newly added parametrized cases, all 7 pass.

### Complete contract and bridge suites

```text
tests/hermes_cli/test_discord_interactions.py +
tests/gateway/test_discord_plugin_interactions.py
179 passed in 46.88s
```

### Ownership/unload regressions

Relevant lifecycle/ownership selection:

```text
9 passed, 18 deselected in 1.53s
```

The complete ownership-ledger file was also run to verify the documented baseline:

```text
24 passed, 3 failed in 10.76s
```

The exact inherited failures are unchanged from the reviewed ledger and are outside Task 6A scope:

- `test_shared_entrypoint_module_uses_the_active_profile_scope`
- `test_provider_overlay_switches_profiles_and_reveals_fresh_global_fallback`
- `test_direct_plugin_platform_registration_infers_immutable_scope`

### Ruff

```text
uv run --no-project --python 3.11 --with ruff ruff check \
  plugins/platforms/discord/plugin_interactions.py \
  tests/hermes_cli/test_discord_interactions.py \
  tests/gateway/test_discord_plugin_interactions.py
All checks passed!
```

### Diff and credential gates

```text
git diff --check
exit 0, no output
```

Final staged credential scan covered exactly the four approved production, test, and report paths. Its JSON output was reduced to match count and matching file paths only; no matching lines or values were printed.

```text
matches=0
paths:
```

## Files changed

- Modified `plugins/platforms/discord/plugin_interactions.py` with the proven minimal consent-read fail-closed fix.
- Modified `tests/hermes_cli/test_discord_interactions.py` with cached-facade revoke/exception coverage.
- Modified `tests/gateway/test_discord_plugin_interactions.py` with lifecycle identity, full unload/reload listener, existing-object runtime revoke/exception coverage, and listener-log capture isolation from completed connection setup.
- Created `.superpowers/sdd/2026-09-01-discord-interactions-core/task-6a-report.md`.

## Self-review

- The production change is limited to the existing bridge dispatch capability gate; registration, codec, ACK rendering, timeout, callback, facade, adapter, and listener implementations are untouched.
- Missing handlers still avoid a capability backend read through the existing short-circuit semantics.
- Capability denial and capability read exceptions converge on the same bounded unavailable-feature ACK without logging or exposing exception text.
- The runtime test mutates capability state only after a successful bridge dispatch and facade send, then reuses the same bridge, registration, custom ID, facade, and adapter.
- Callback and adapter assertions prove the denial occurs before a second callback or adapter await.
- Clearing `caplog` after successful connection setup narrows the two existing redaction assertions to listener dispatch; it does not filter, weaken, or alter listener ERROR checks.
- Disposal and targeted unload each prove registration A becomes inactive and the accessor returns `None` before handler B is registered.
- Full unload proves the handler map is empty before one fresh registration and listener dispatch.
- Tests use generic plugin IDs and `no_change`; no quiz/session/question/domain type or plugin-repository/deployment/live Discord behavior was introduced.
- No dependency, cross-platform abstraction, View restoration registry, push, merge, force/reset, or history rewrite was performed.

## Concerns

- The three ownership-ledger failures above remain the explicitly documented pre-existing baseline and were not silently changed.
- The inherited `asyncio.to_thread` timeout limitation remains unchanged and is not load-bearing for Task 6A.
