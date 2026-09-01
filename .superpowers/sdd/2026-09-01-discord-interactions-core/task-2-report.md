# Core Task 2 implementation report

## Status

**DONE_WITH_CONCERNS**

Core Task 2 is implemented and task-scoped verification is green. Three unrelated multi-profile registry tests in the broader `test_plugin_ownership_ledger.py` file fail on this Windows checkout; they reproduce individually and touch registry behavior outside the approved Task 2 diff. Details are under **Concerns**.

## Scope completed

- Added the canonical, default-off `gateway.discord_interactions` capability with legacy gate `allow_discord_interactions`.
- Added `PluginContext.register_discord_interaction(handler)` with callable validation, capability enforcement, canonical `ctx.plugin_id` ownership, collision rejection, and `PluginRegistration` lifecycle tracking.
- Added manager-instance-local `plugin_id -> handler` storage and `PluginManager.get_discord_interaction_handler(plugin_id)`.
- Added identity-conditional disposal plus targeted unload and full unload cleanup.
- Added registration default-off, callable, canonical-ID collision, manager isolation, dispose identity, targeted unload, and full lifecycle tests.
- Inspected the inherited codec and validators against the Task 2 brief, reviewer constraints, and binding RFC. They satisfy the specified strict alphabets, canonical base64/UTF-8 fail-closed decoding, 90-character custom-ID budget, JSON-copy/non-mutation behavior, top-level schema rejection, Discord field budgets, and interaction/result combinations. No concrete defect was found, so they were not rewritten.
- Did not add Task 3 facade/send/update, adapter, dispatch, ACK, or quiz-domain behavior.

## Evidence inherited from the controller

The following evidence predates this takeover and was not rerun before the capability change; it is recorded exactly as controller-verified:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py
1 failed, 60 passed in 53.55s
failure: test_discord_interactions_capability_is_registered
expected RED: KeyError: 'gateway.discord_interactions'
```

The controller also verified `git diff --check` before takeover. At takeover, `HEAD` was `be6373ceb6034b20962bc51fec68fde200bb1df5` and only these intentional files were untracked:

```text
hermes_cli/discord_interactions.py
tests/hermes_cli/test_discord_interactions.py
```

## RED/GREEN evidence produced during this takeover

### Capability GREEN

After adding only the canonical capability:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py::test_discord_interactions_capability_is_registered
1 passed in 2.78s
```

### Registration/lifecycle RED

Registration tests were added before `PluginContext` or `PluginManager` production changes:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py -k 'registration or unload'
7 failed, 61 deselected in 2.16s
```

All seven failures were the expected missing-feature failure:

```text
AttributeError: 'PluginContext' object has no attribute 'register_discord_interaction'
```

### Registration/lifecycle GREEN

After the minimal manager-local implementation:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py -k 'registration or unload'
7 passed, 61 deselected in 4.78s
```

After capturing the canonical plugin ID in the release closure, the focused suite remained green:

```text
7 passed, 61 deselected in 2.84s
```

## Verification

### Contract tests

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py
68 passed in 15.41s
```

### Contract plus canonical capability regressions

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_discord_interactions.py tests/hermes_cli/test_plugin_capabilities.py
107 passed in 39.92s
```

### Relevant PluginContext/PluginManager lifecycle regressions

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q \
  tests/hermes_cli/test_plugin_ownership_ledger.py::test_load_force_reload_and_unload_remove_every_manager_registration \
  tests/hermes_cli/test_plugin_ownership_ledger.py::test_manager_local_override_does_not_resurrect_after_targeted_unload \
  tests/hermes_cli/test_plugin_ownership_ledger.py::test_spawned_supervised_task_is_cancelled_on_unload \
  tests/hermes_cli/test_plugin_ownership_ledger.py::test_on_unload_exception_does_not_block_other_teardown
4 passed in 4.59s
```

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugins.py -k 'force_reload or unload_all'
2 passed, 63 deselected in 7.15s
```

### Static and diff checks

```text
uv run --no-project --python 3.11 --with ruff ruff check \
  hermes_cli/discord_interactions.py \
  hermes_cli/plugin_capabilities.py \
  hermes_cli/plugins.py \
  tests/hermes_cli/test_discord_interactions.py
All checks passed!
```

```text
git diff --check
(exit 0, no output)
```

## Files created or modified

- Created `hermes_cli/discord_interactions.py` (inherited untracked implementation, inspected and preserved).
- Modified `hermes_cli/plugin_capabilities.py`.
- Modified `hermes_cli/plugins.py`.
- Created/extended `tests/hermes_cli/test_discord_interactions.py` (inherited untracked tests plus takeover registration/lifecycle tests).
- Created this report.

## Self-review

- The registry is a `PluginManager` field, not process-global.
- Registration uses only the host-derived canonical `ctx.plugin_id`; no plugin-supplied namespace exists.
- The release closure removes the mapping only when the exact registered handler is still current.
- The existing ownership ledger drives `dispose()` and targeted/full unload behavior.
- Full unload explicitly resets the manager-local map.
- Capability checking is default-off and occurs before registration succeeds.
- No new dependency or cross-platform interaction abstraction was introduced.
- The production diff is limited to the approved Task 2 files.

## Concerns

A diagnostic run of the entire ownership-ledger file returned `3 failed, 24 passed`. Each failure reproduces individually:

- `test_shared_entrypoint_module_uses_the_active_profile_scope`
- `test_provider_overlay_switches_profiles_and_reveals_fresh_global_fallback`
- `test_direct_plugin_platform_registration_infers_immutable_scope`

The failures are in pre-existing multi-profile tool/provider/platform registry scope behavior. Task 2 changes do not modify `tools.registry`, image-provider registry, platform registry, or Hermes-home override handling, and all directly relevant lifecycle regressions pass. Per the approved narrow scope, no unrelated fix was attempted.
