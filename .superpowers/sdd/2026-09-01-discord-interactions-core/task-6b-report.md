# Core Task 6B report — restart routing, forged inputs, and full core gate

## Status

**DONE_WITH_CONCERNS**

Task 6B hardening is complete with tests and this report only. All new focused cases passed against the reviewed production implementation, so no focused RED proved a production defect and no production file was changed.

The concerns are inherited and intentionally deferred: 14 Minor test/documentation findings already ledgered across nine prior review entries, the documented `asyncio.to_thread` timeout limitation, and three pre-existing ownership-ledger failures. None is load-bearing for the Task 6B gate.

## Commit range and scope

- Task 6B base: `0fb17fddded18c7f1aa0eba7918100bc8b0a0765`.
- Task 6B feature range: `0fb17fddded18c7f1aa0eba7918100bc8b0a0765..HEAD`, where `HEAD` is the single Task 6B tests/report commit containing this file.
- Approved scope: `tests/gateway/test_discord_plugin_interactions.py` and this report.
- Production changes: none.
- `tests/hermes_cli/test_discord_interactions.py`: unchanged; the required codec behavior was already covered and the bridge-level fail-closed assertions belong in the gateway suite.
- No plugin repository, quiz implementation, deployment, live Discord write, dependency change, merge, push, force/reset, or history rewrite was performed.
- Final whole-branch review was not performed; it remains the next tranche-level approval gate.

## Hardening added

The gateway suite gained 10 collected cases and strengthened one existing reconnect case:

1. A stable custom ID is rendered before restart, then dispatched through a different fresh bridge and fresh fake Discord client with empty message/View caches while the same active-profile manager registration remains authoritative.
2. An unknown but canonically encoded plugin ID follows the missing-handler fail-closed path, sends one bounded ephemeral unavailable ACK, does not consult capability state, and invokes no known callback.
3. Four malformed/noncanonical custom-ID variants fail at codec validation before auth, manager lookup, or callback:
   - padded/noncanonical plugin segment;
   - noncanonical action;
   - malformed route token;
   - padded/noncanonical component-value segment.
4. A generic fixture handler validates an expected route-token/channel/message tuple. Independent valid mutations of route token, channel, and message each reach the fixture exactly once and receive the same deterministic stale rejection. Core owns no quiz/session/question/domain type.
5. An independent optional `component_value` mutation preserves plugin/action/route identity while changing exactly the normalized payload value; the generic fixture accepts the original and deterministically rejects the mutation.
6. The reconnect test now dispatches once through the first client and once through the fresh reconnect client, proving one listener and one bridge dispatch per client with no duplicate callback.

## Focused evidence

Command selection covered the new restart, unknown-ID, malformed-ID, stale-tuple, component-value, and strengthened reconnect cases.

```text
11 passed, 53 deselected in 1.18s
```

The 11 selected passes comprise the 10 newly collected parameter/case instances plus the strengthened pre-existing reconnect test.

All focused cases were GREEN on first execution against production. This is hardening coverage of already-correct reviewed behavior, not a claimed RED→GREEN production fix.

## Complete seven-file core gate

The exact seven files from the original Task 6 brief were run together:

```text
tests/hermes_cli/test_plugin_config_state_bridge.py
tests/hermes_cli/test_plugin_capabilities.py
tests/hermes_cli/test_discord_interactions.py
tests/gateway/test_discord_plugin_interactions.py
tests/gateway/test_discord_clarify_buttons.py
tests/gateway/test_discord_component_auth.py
tests/gateway/test_discord_platform_events.py
```

Result:

```text
297 passed in 95.51s (0:01:35)
```

### Programmatic unchanged-baseline proof

Before the baseline run, `git diff --quiet` verified that these three files are unchanged from Task 6B base `0fb17fddded18c7f1aa0eba7918100bc8b0a0765`:

```text
tests/gateway/test_discord_clarify_buttons.py
tests/gateway/test_discord_component_auth.py
tests/gateway/test_discord_platform_events.py
```

They were then run independently with JUnit XML output. A Python XML parser computed pass count and asserted `passed >= 41` with zero failures, errors, or skips:

```text
41 passed in 8.01s
baseline_tests=41 baseline_passed=41 failures=0 errors=0 skipped=0
```

## Ruff and diff gates

Ruff covered the four approved core production modules and both core interaction test modules:

```text
hermes_cli/discord_interactions.py
hermes_cli/plugins.py
plugins/platforms/discord/adapter.py
plugins/platforms/discord/plugin_interactions.py
tests/hermes_cli/test_discord_interactions.py
tests/gateway/test_discord_plugin_interactions.py

All checks passed!
```

`git diff --check` exited 0 with no output.

Final feature-range status/stat and the staged credential result are recorded below after staging this report and the approved test file.

## Files changed

- Modified `tests/gateway/test_discord_plugin_interactions.py`.
- Created `.superpowers/sdd/2026-09-01-discord-interactions-core/task-6b-report.md`.

No production or baseline Discord file changed.

## Known limitations and deferred findings

The SDD progress ledger records 14 deferred Minor findings across nine prior review entries. Task 6B preserves them for the explicitly required final whole-branch review:

- Task 1: backup missing-key/default/read-only primary-byte coverage.
- Task 2: direct default-off capability-registry coverage.
- Task 3 (2): existing-facade revoke coverage and send validated-copy identity coverage. Later Task 6A coverage strengthens the revoke seam, but the ledger entry is not silently rewritten here.
- Task 4 (2): callback-free real-SDK/instance-override coverage and fuller render/log assertion coverage.
- Task 5A-1: exhaustive negative ACK-surface assertions.
- Task 5A-2a (3): duplicate valid modal-ID last-wins coverage, exact malformed-normalization counts, and direct SDK `discord.NotFound` unknown-interaction coverage.
- Task 5A-2b1: direct modal-submit 90-second timeout-selection coverage.
- Task 5A-2b2: direct deferred-timeout trace/log correlation coverage.
- Task 5B (2): combined existing-handler/additive-listener dispatch simulation and independently populated private interaction-surface redaction fixtures.

Documented timeout limitation:

- `asyncio.wait_for` bounds host wait/response latency, but cancellation cannot terminate an already-running synchronous callback in `asyncio.to_thread` or roll back its side effects. Strict prevention requires cooperative deadlines/idempotent durable transitions or process isolation.

Pre-existing ownership-ledger baseline failures, already proven unchanged at prior base/head comparisons:

- `test_shared_entrypoint_module_uses_the_active_profile_scope`
- `test_provider_overlay_switches_profiles_and_reveals_fresh_global_fallback`
- `test_direct_plugin_platform_registration_infers_immutable_scope`

These three tests are outside the named seven-file core gate and were not modified or reclassified by Task 6B.

## Self-review

- The diff is tests/report only; production remains byte-for-byte unchanged from the Task 6B base.
- Unknown canonical plugin IDs and malformed/noncanonical wire IDs are asserted as distinct paths.
- Codec failures are proven to stop before auth and manager lookup; missing handlers are proven to stop before capability lookup and callback.
- The stale route/channel/message policy lives entirely in a generic fixture callback; core only normalizes and routes the versioned payload.
- Each stale tuple field is mutated independently from the same expected tuple and receives the same deterministic rejection.
- `component_value` is mutated independently while plugin ID, action, route token, user, channel, and message routing remain fixed.
- Stable routing is derived only from the custom ID and active manager registration; the fresh fake client has empty message and persistent-View caches.
- Reconnect assertions cover both old and new clients and require exactly one listener and one bridge dispatch per client.
- New tests assert ACK/callback negatives on hostile paths and exact callback/payload behavior on valid-but-stale paths.
- No quiz/session/question/domain type or plugin implementation entered core or the generic fixture.
- No external write or final whole-branch review was performed.

## Final audit evidence

Final staged feature range from `0fb17fddded18c7f1aa0eba7918100bc8b0a0765`:

```text
A  .superpowers/sdd/2026-09-01-discord-interactions-core/task-6b-report.md
M  tests/gateway/test_discord_plugin_interactions.py
2 files changed, 419 insertions(+), 3 deletions(-)
```

Exact per-file staged numstat:

```text
175  0  .superpowers/sdd/2026-09-01-discord-interactions-core/task-6b-report.md
244  3  tests/gateway/test_discord_plugin_interactions.py
```

`detect-secrets` scanned exactly those two approved staged paths. Its JSON output was reduced to match count and matching paths only; no matching lines or values were printed:

```text
matches=0
paths:
```

The staged `git diff --check` exited 0 with no output. After commit, the same range is one Task 6B commit and the worktree cleanliness check is reported in the caller-visible completion summary.
