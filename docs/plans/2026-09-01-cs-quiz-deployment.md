# `cs-quiz` Discord and Cron Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to execute this operational plan one approval-gated task at a time. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the verified `cs-quiz` plugin into the active Hermes profile, create one isolated Discord channel, prove the live interaction flow, and schedule one daily 20:00 Asia/Seoul start without activating anything before explicit approval.

**Architecture:** Deployment is a sequence of reversible, read-back-verified writes. The plugin is installed disabled, configured with `pilot_enabled=false`, enabled with explicit capability consent, and loaded by the gateway. The daily cron is created and paused while the plugin is fail-closed; after final approval it is resumed and run once manually, and that first real session establishes pilot date D while doubling as the live smoke.

**Tech Stack:** Hermes plugin/config/gateway CLI, Hermes `cronjob` tool, Discord admin/read tools, live Discord gateway, git-installed source repository.

**Spec:** `docs/rfcs/2026-09-discord-plugin-interactions-cs-quiz.md`

## Global Constraints

- Guild is exactly `1540532676694642769` (`고슬고슬`).
- New text channel name is exactly `컴퓨터공학-퀴즈`.
- Plugin source is exactly `C:\Users\nasca\AppData\Local\hermes-plugins\cs-quiz`.
- Active profile is `default`; do not edit another profile.
- Do not hand-edit `config.yaml`; use `hermes config get/set`.
- Do not display bot token, LLM credentials, or secret values.
- Every external write requires a fresh approval immediately before execution and exact-target read-back after execution.
- Discord channel creation, permission overwrite, plugin install/enable, capability grant, config set, gateway reload, message smoke, cron create/pause/resume/remove, and pilot enable are separate write classes.
- `pilot_enabled` remains false until the user names the start local date and approves activation.
- The first live manual session is D and is the live smoke. This refines RFC §21 because a disabled pilot cannot perform a live interaction smoke without adding an unnecessary smoke-only interface.
- Do not create language or math quiz channels/jobs.
- Do not push either repository without explicit remote-write approval.

## Runtime Values Captured by Read-Back

These are runtime outputs, not guessed constants:

- `OWNER_USER_ID`: exact Discord ID of 강현성, verified from the triggering account/guild membership.
- `BOT_USER_ID`: exact connected Hermes bot ID.
- `QUIZ_CHANNEL_ID`: returned by Discord channel creation and re-read from guild channels.
- `INSTALL_COMMIT`: exact 40-character source repository HEAD passed to `hermes plugins install --ref`.
- `CRON_JOB_ID`: returned by cron creation and re-read from `cronjob(action="list")`.
- `PILOT_DATE_D`: local date explicitly approved by the user.

Never replace a malformed or absent runtime value with a plausible-looking one.

---

### Task 1: Read-only deployment preflight

**Files:**
- Read only: core repo, plugin repo, active Hermes profile, Discord guild, cron list.

**Interfaces:**
- Consumes: verified core/plugin tranche outputs.
- Produces: preflight report and exact runtime IDs; no writes.

- [ ] **Step 1: Verify both repositories are clean and identify commits**

Run:

```bash
git -C C:/Users/nasca/AppData/Local/hermes/hermes-agent status --short
git -C C:/Users/nasca/AppData/Local/hermes/hermes-agent rev-parse HEAD
git -C C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz status --short
git -C C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz rev-parse HEAD
```

Expected: both status outputs empty; record both full commit IDs. Stop if either worktree is dirty.

- [ ] **Step 2: Re-run artifact gates**

Core:

```bash
cd C:/Users/nasca/AppData/Local/hermes/hermes-agent
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/hermes_cli/test_plugin_config_state_bridge.py \
  tests/hermes_cli/test_plugin_capabilities.py \
  tests/hermes_cli/test_discord_interactions.py \
  tests/gateway/test_discord_plugin_interactions.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py \
  tests/gateway/test_discord_platform_events.py
```

Plugin:

```bash
cd C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q
hermes plugins doctor C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz --ci
```

Stop on any failure; do not “deploy and see.”

- [ ] **Step 3: Read current Hermes and cron state**

```bash
hermes config get timezone
hermes config get discord.free_response_channels
hermes config get plugins.entries.cs-quiz.settings.pilot_enabled
hermes plugins list --json
hermes plugins capabilities cs-quiz
hermes gateway status
hermes cron list
```

The capability command may correctly report “not installed”; record that as pre-install state. Do not print config sections that may contain secrets.

- [ ] **Step 4: Read Discord guild state**

Use Discord read/admin-read operations to list guild channels, roles, bot identity, and members. Verify:

```text
guild_id == 1540532676694642769
exactly one human member matches 강현성 -> OWNER_USER_ID
connected bot identity -> BOT_USER_ID
no existing channel named 컴퓨터공학-퀴즈
```

If the channel already exists, stop and ask whether to reuse it; do not create a duplicate.

- [ ] **Step 5: Assess timezone blast radius**

Hermes cron uses `hermes_time.now()`: `HERMES_TIMEZONE`, then top-level config `timezone`, then system-local timezone. List existing cron jobs before changing timezone. If effective timezone is already `Asia/Seoul`, make no change. If it differs, report every existing cron job whose wall-clock interpretation would change and obtain separate approval before `hermes config set timezone Asia/Seoul`.

- [ ] **Step 6: Present preflight evidence and request install approval**

Report commit IDs, test counts, doctor result, current timezone, current free-response channel IDs, existing cron count, guild/member/channel facts, and proposed first write. Stop for user approval.

---

### Task 2: Install disabled, configure disabled, and verify discovery

**Files:**
- External state: active profile plugin install/config only.

**Interfaces:**
- Consumes: approved plugin commit and runtime owner/guild IDs.
- Produces: installed but pilot-disabled plugin with settings read-back.

- [ ] **Step 1: After explicit approval, install from the local git source disabled**

```bash
INSTALL_COMMIT="$(git -C C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz rev-parse HEAD)"
test "${#INSTALL_COMMIT}" -eq 40
hermes plugins install file:///C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz --ref "$INSTALL_COMMIT" --no-enable
```

This copies/clones through the supported installer and runs plugin guard. Do not copy files directly into the active profile plugin directory.

- [ ] **Step 2: Read back installation before configuration**

```bash
hermes plugins list --user --json
hermes plugins doctor cs-quiz --ci
hermes plugins capabilities cs-quiz
```

Verify source/install metadata identifies the intended plugin and commit; status is disabled; declared capability is `gateway.discord_interactions` and not yet granted.

- [ ] **Step 3: After separate config-write approval, set safe settings**

At this point `QUIZ_CHANNEL_ID` does not exist, so set only values already verified:

```bash
hermes config set plugins.entries.cs-quiz.settings.pilot_enabled false
hermes config set plugins.entries.cs-quiz.settings.owner_user_id "$OWNER_USER_ID"
hermes config set plugins.entries.cs-quiz.settings.guild_id "1540532676694642769"
```

`$OWNER_USER_ID` denotes the exact verified runtime value captured in Task 1; type it from the read-back, never infer it.

- [ ] **Step 4: Read back only non-secret settings**

```bash
hermes config get plugins.entries.cs-quiz.settings.pilot_enabled
hermes config get plugins.entries.cs-quiz.settings.owner_user_id
hermes config get plugins.entries.cs-quiz.settings.guild_id
```

Expected: `false`, exact owner ID, exact guild ID. Stop if any differs.

- [ ] **Step 5: Request capability/enable approval**

Explain that `gateway.discord_interactions` allows the trusted in-process plugin to register Discord callbacks and send/update interactive messages; it is consent, not a sandbox. Stop for approval.

- [ ] **Step 6: Enable interactively and grant only the declared capability**

Run in a PTY so the consent prompt is visible:

```bash
hermes plugins enable cs-quiz
```

Approve only `gateway.discord_interactions`. Do not grant tool override or any undeclared capability.

- [ ] **Step 7: Read back enabled and granted state**

```bash
hermes plugins list --enabled --json
hermes plugins capabilities cs-quiz
```

Verify enabled, declared=granted for exactly the one capability, and `pilot_enabled=false` remains unchanged.

---

### Task 3: Create and configure the dedicated Discord channel

**Files:**
- External state: Discord guild plus one Hermes config key.

**Interfaces:**
- Consumes: exact guild, owner, and bot IDs.
- Produces: one channel ID, reviewed permissions, channel-only mention-free setting.

- [ ] **Step 1: Present the exact channel payload and request creation approval**

Payload fields:

```text
guild_id: 1540532676694642769
name: 컴퓨터공학-퀴즈
type: text
topic: 청춘케어 실무 기반 컴퓨터공학 일일 퀴즈
```

Do not add permission overwrites until the current guild role/member model has been shown. Recommend the smallest policy that lets the owner and Hermes bot view/send/interact while preserving the server's existing visibility intent.

- [ ] **Step 2: Create exactly one channel after approval**

Use the Discord admin create-channel operation with explicit guild/name/type/topic. Capture returned `QUIZ_CHANNEL_ID`.

- [ ] **Step 3: Read back the exact channel**

List guild channels and fetch the returned channel. Verify ID, name, type, parent/category, topic, and permission overwrites. If any field differs, stop before config changes.

- [ ] **Step 4: If permission changes are needed, request a separate approval**

Show the current and proposed overwrites with role/member IDs and allowed/denied permissions. Apply only after approval, then fetch the channel again. Never guess the bot role.

- [ ] **Step 5: Set plugin channel setting after config-write approval**

```bash
hermes config set plugins.entries.cs-quiz.settings.channel_id "$QUIZ_CHANNEL_ID"
hermes config get plugins.entries.cs-quiz.settings.channel_id
```

Verify the exact returned channel ID.

- [ ] **Step 6: Preserve and extend mention-free channels**

Read `discord.free_response_channels` again immediately before writing. Normalize its string/list to unique string IDs and append only `QUIZ_CHANNEL_ID`:

```bash
OLD_FREE_RESPONSE_JSON="$(hermes config get discord.free_response_channels --json)"
export OLD_FREE_RESPONSE_JSON QUIZ_CHANNEL_ID
NEW_FREE_RESPONSE_JSON="$(python3 -c "import json,os; v=json.loads(os.environ['OLD_FREE_RESPONSE_JSON']); xs=v if isinstance(v,list) else [x.strip() for x in str(v or '').split(',') if x.strip()]; cid=os.environ['QUIZ_CHANNEL_ID']; print(json.dumps(list(dict.fromkeys([str(x) for x in xs]+[cid]))))")"
printf 'Before: %s\nAfter:  %s\n' "$OLD_FREE_RESPONSE_JSON" "$NEW_FREE_RESPONSE_JSON"
```

Show the complete before/after values and obtain approval. Then write the exact computed value and read it back:

```bash
test -n "$NEW_FREE_RESPONSE_JSON"
hermes config set discord.free_response_channels "$NEW_FREE_RESPONSE_JSON"
hermes config get discord.free_response_channels --json
```

Read-back must contain all prior IDs plus exactly the new quiz channel, with no other channel changes.

---

### Task 4: Load the plugin into the live gateway without starting the pilot

**Files:**
- External state: running gateway process.

**Interfaces:**
- Consumes: enabled plugin, granted capability, complete settings, pilot false.
- Produces: live registration read-back with no quiz session/message.

- [ ] **Step 1: Verify safe pre-reload state**

```bash
hermes config get plugins.entries.cs-quiz.settings.pilot_enabled
hermes gateway status
```

Expected: `false` and gateway currently healthy. Confirm no active `quiz` state and no quiz message in the channel.

- [ ] **Step 2: Request gateway reload approval**

Warn that the Discord bot may disconnect briefly and this conversation could be interrupted. Agree on a quiet window. Do not treat earlier restart approvals as current approval.

- [ ] **Step 3: Reload once and follow the verified Windows fallback only if needed**

Primary command:

```bash
hermes gateway restart
```

If it exits nonzero, do not repeat it. Inspect status/logs and use the existing Windows scheduled-task recovery procedure only after reporting the failure and obtaining approval for that fallback.

- [ ] **Step 4: Verify live state**

```bash
hermes gateway status
hermes plugins list --enabled --json
hermes plugins capabilities cs-quiz
hermes config get plugins.entries.cs-quiz.settings.pilot_enabled
```

Read recent gateway logs without displaying secret-bearing lines. Verify Discord reconnect, plugin registration, no collision/error, and pilot still false. Post no quiz message.

- [ ] **Step 5: Verify mention-free general chat with one harmless message**

After message-send approval, the owner sends a plain non-mention question in the quiz channel. Verify Hermes replies in that channel and other channels' mention behavior remains unchanged. This checks general channel routing only; it does not start the quiz.

---

### Task 5: Create and pause the daily cron while the pilot is disabled

**Files:**
- External state: Hermes cron table.

**Interfaces:**
- Consumes: live plugin, `pilot_enabled=false`, effective Asia/Seoul timezone.
- Produces: one paused recurring job and exact `CRON_JOB_ID`.

- [ ] **Step 1: Reconfirm timezone and fail-closed state**

```bash
hermes config get timezone --json
hermes config get plugins.entries.cs-quiz.settings.pilot_enabled
hermes cron list --all
```

Expected: effective timezone is `Asia/Seoul`, pilot is `false`, and no job named `cs-quiz-daily-20-kst` exists. If timezone differs, report every existing cron job affected and obtain separate approval before changing the global timezone.

- [ ] **Step 2: Show the exact cron payload and request create-and-pause approval**

```json
{
  "action": "create",
  "name": "cs-quiz-daily-20-kst",
  "schedule": "0 20 * * *",
  "prompt": "Call the cs_quiz_start tool exactly once with an empty argument object. Do not generate quiz questions in chat. Report only the tool's structured status. If the tool is unavailable or returns an error, report the exact non-secret error and do not retry in the same run.",
  "deliver": "local",
  "enabled_toolsets": ["cs_quiz"],
  "attach_to_session": false
}
```

Explain that `deliver=local` prevents duplicate Discord chatter because the plugin sends the card. Creation is followed immediately by pause, and pilot false makes an accidental fire fail closed. Do not execute within five minutes of 20:00 Asia/Seoul.

- [ ] **Step 3: Create once and pause the returned ID immediately**

Call `cronjob` with the exact create payload, capture its returned `CRON_JOB_ID`, then call:

```text
cronjob(action="pause", job_id=CRON_JOB_ID)
```

Do not guess or transcribe a different job ID.

- [ ] **Step 4: Read back every cron invariant**

Call `cronjob(action="list")`. Verify exact ID/name/schedule/prompt/delivery/toolset, paused state, and exactly one job with the name. Pilot must still read false and no quiz message/session may exist.

- [ ] **Step 5: Record rollback without executing it**

```text
remove: cronjob(action="remove", job_id=CRON_JOB_ID)
```

Removal needs fresh approval.

---

### Task 6: Final pilot activation, manual cron run, and live smoke (date D)

**Files:**
- External state: plugin setting, cron state, Discord messages, plugin durable state.

**Interfaces:**
- Consumes: paused verified job and exact Asia/Seoul local date.
- Produces: one resumed recurring job, first real pilot session D, and live smoke evidence.

- [ ] **Step 1: Present D and the exact writes**

Show current Asia/Seoul date as `PILOT_DATE_D`. Explain that `pilot_enabled=true`, cron resume, and one manual run start the immutable 30-calendar-day window D..D+29; failed/incomplete days count. Ask for explicit approval for those three writes.

- [ ] **Step 2: Enable the pilot and read back before touching cron**

```bash
hermes config set plugins.entries.cs-quiz.settings.pilot_enabled true
hermes config get plugins.entries.cs-quiz.settings.pilot_enabled
```

Expected: `true`, with owner/guild/channel settings unchanged.

- [ ] **Step 3: Resume the exact job and verify its next fire**

```text
cronjob(action="resume", job_id=CRON_JOB_ID)
cronjob(action="list")
```

Verify the captured job alone is enabled and its next scheduled fire is 20:00 Asia/Seoul. If current time is within five minutes of 20:00, pause it and choose another activation window rather than racing the scheduler.

- [ ] **Step 4: Run the exact job once manually**

```text
cronjob(action="run", job_id=CRON_JOB_ID)
```

The run is asynchronous. Do not poll or issue a second run while it is active. When its result returns, verify one loading card becomes one first-question card and one active session has `local_date == PILOT_DATE_D`.

- [ ] **Step 5: Complete the live smoke in the real session**

Verify with the owner:

```text
owner multiple-choice click -> immediate feedback
same logical button double-click -> one attempt
designated second tester click -> ephemeral rejection, no state mutation
wrong base -> supplement button -> one supplement -> next
short-answer button -> Modal -> deterministic grading
essay button -> Modal -> one rubric grade
valid stale component -> current durable view re-render
```

Gateway restart/resume is a separate approval. If approved, restart once mid-session and verify the old message custom ID resumes through empty cache. Finish all 10 base questions and triggered supplements to verify component disable and summary.

The unauthorized live probe requires a real second tester who explicitly agrees to click once. If none is available, do not impersonate one; record the live case as not exercised and rely on the passing deterministic authorization tests from the core/plugin tranches.

- [ ] **Step 6: Verify same-date idempotency after completion**

After separate approval, call `cronjob(action="run", job_id=CRON_JOB_ID)` once more. It must return/reconcile the same D session and create no second message/session. Read back the latest run and plugin state.

- [ ] **Step 7: Read back pilot and cron state**

Locate the single profile `plugin-data/*/state.json` whose parsed root contains `quiz`, without printing file content. In a Python process, build a new dict containing only session ID SHA-256/length, local date, status, attempt counts, desired/rendered revisions, `render_pending`, and serialized state byte size. Before printing, recursively reject output keys containing `answer`, `feedback`, `rubric`, `prompt`, or `token`. Do not print raw state, raw answers, or answer keys.

- [ ] **Step 8: Stop safely on any smoke failure**

First pause the exact cron after approval, then set `pilot_enabled=false` after approval. Preserve state/logs and return to implementation diagnosis. Do not remove the job or state automatically.

- [ ] **Step 9: Record rollback handles and stop at the deployment gate**

Report without executing:

```text
pause:  cronjob(action="pause", job_id=CRON_JOB_ID)
resume: cronjob(action="resume", job_id=CRON_JOB_ID)
remove: cronjob(action="remove", job_id=CRON_JOB_ID)
plugin stop: hermes config set plugins.entries.cs-quiz.settings.pilot_enabled false
plugin disable: hermes plugins disable cs-quiz
```

Removal and disable each require fresh approval.
Report channel ID, plugin installed commit, capability/settings read-back, gateway status, D, live smoke evidence, cron ID/next run, and remaining risk. Do not alter unrelated Discord channels, jobs, or profiles. Do not push without explicit approval.
