# Core Task 1 Report — Generic process-safe plugin-state CAS

## Status

DONE

## Scope completed

Implemented only Core Task 1 from `task-1-brief.md`:

- Added `PluginState.compare_and_set(key, expected, value) -> bool`.
- Added `PluginState.get_backup(key, default=None) -> Any` for read-only inspection.
- Extracted `_write_unlocked(previous, data)` and reused it from both `set()` and CAS while holding the existing cross-process lock.
- Preserved exactly one previous-good full-state generation at `_백업_원본_state.json` before replacing an existing primary state file.
- Kept JSON serialization validation, the 10 MiB per-plugin quota, mode `0o600`, and atomic JSON replacement behavior.
- Added equality/mismatch, absent-key, quota preservation, corrupt-primary refusal, exact backup generation, and two-process single-winner coverage.
- Did not implement Discord interactions, quiz domain state, cron behavior, or any later task.

## Strict TDD evidence

### RED

Added `test_state_compare_and_set_commits_only_matching_snapshot` first, then ran the exact focused command from the brief:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugin_config_state_bridge.py::test_state_compare_and_set_commits_only_matching_snapshot
```

Observed the expected failure:

```text
AttributeError: 'PluginState' object has no attribute 'compare_and_set'
1 failed
```

After adding the edge/recovery tests, the backup test also failed for the expected missing interface:

```text
AttributeError: 'PluginState' object has no attribute 'get_backup'
1 failed, 3 passed
```

### GREEN

Implemented the minimum generic locked writer/CAS behavior and reran the focused equality/mismatch test:

```text
1 passed in 1.90s
```

Implemented the read-only backup accessor and reran the four edge/recovery tests:

```text
4 passed in 13.03s
```

Added the two-process barrier test and verified the existing locked CAS implementation produces exactly one winner:

```text
1 passed in 5.63s
```

### Refactor/self-review

- Kept one shared `_write_unlocked()` path for serialization, quota enforcement, backup, and primary replacement.
- Confirmed CAS mismatch returns before any write or backup update.
- Confirmed corrupt primary parsing raises without modifying the primary.
- Confirmed the backup reader uses the same state lock and never writes primary state.
- Confirmed the backup glob contains one generation only.
- Confirmed `git diff --check` is clean and no repository `*.tmp` files remain.
- Confirmed the diff is limited to the generic PluginState implementation, its focused tests, and this report.

## Files changed

- `hermes_cli/plugins.py`
- `tests/hermes_cli/test_plugin_config_state_bridge.py`
- `.superpowers/sdd/2026-09-01-discord-interactions-core/task-1-report.md`

## Verification

Focused task file:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugin_config_state_bridge.py
27 passed in 61.51s
```

Relevant plugin regressions:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugins.py tests/plugins/test_security_guidance_plugin.py
85 passed in 50.10s
```

Targeted lint:

```text
uv run --no-project --python 3.11 --with ruff ruff check hermes_cli/plugins.py tests/hermes_cli/test_plugin_config_state_bridge.py
All checks passed!
```

## Concerns

None.

## Fix Round 1 — Backup-preservation regression coverage

### Status

DONE

### Changes

- Updated the oversized-CAS regression to first create an existing previous-good backup, capture the primary and backup bytes, and assert both remain byte-identical after the quota failure.
- Added the equivalent regression for a non-JSON-serializable CAS value.
- Production code was not changed because both focused regressions passed against the existing implementation.

### Files changed

- `tests/hermes_cli/test_plugin_config_state_bridge.py`
- `.superpowers/sdd/2026-09-01-discord-interactions-core/task-1-report.md`

### Verification

Focused amended test file:

```text
uv run --no-project --python 3.11 --with-editable . --with pytest pytest -q tests/hermes_cli/test_plugin_config_state_bridge.py
............................                                             [100%]
28 passed in 67.23s (0:01:07)
```

Targeted lint:

```text
uv run --no-project --python 3.11 --with ruff ruff check tests/hermes_cli/test_plugin_config_state_bridge.py
All checks passed!
```

### Commit

- Regression-test fix: `1fb6504760bf7e396cba24426f556ecd7f9064c9`

### Concerns

None. The explicitly deferred Minor `get_backup` default finding was not addressed.
