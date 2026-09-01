# `cs-quiz` 30-Day Pilot Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` for the D+30 operational review. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the D+30 report against durable state, present quantitative and qualitative evidence, and let 강현성 decide whether to stop, revise, continue, or separately design language/math quizzes.

**Architecture:** The daily `cs_quiz_start` call detects D+30 and posts one idempotent evaluation card instead of a new quiz. A separate read-only audit recomputes every metric from the same durable state without printing answers. No threshold makes the decision automatically; the user owns the final decision and every follow-up write.

**Tech Stack:** `cs_quiz.engine.build_evaluation()`, Hermes cron run history, profile-scoped plugin JSON state, Python 3.11 stdlib, Discord read-only inspection.

**Spec:** `docs/rfcs/2026-09-discord-plugin-interactions-cs-quiz.md`

## Global Constraints

- Pilot window is local date D through D+29 inclusive; failed, expired, and incomplete days count.
- D+30 20:00 creates no quiz and posts/reconciles one evaluation card.
- No raw answer, answer key, Discord token, credential, or personal message text is printed in audit output.
- Zero denominators render `측정 불가`, not `0%`.
- No automatic pass/fail threshold.
- No automatic language, programming-language, or math quiz creation.
- Pausing/removing cron, disabling plugin/pilot, resetting state, or creating a follow-up project each requires explicit user approval.
- Existing state is preserved even if the pilot stops.

## Evaluation Formulas

```text
completion_days
  = count(session.status == completed for sessions with D <= local_date <= D+29)

base_questions
  = count(first committed base-answer attempts in the pilot window)

supplement_questions
  = count(first committed supplement-answer attempts in the pilot window)

domain_accuracy[domain]
  = correct committed base attempts / graded committed base attempts

first_half_accuracy[domain]
  = domain accuracy for D..D+14

second_half_accuracy[domain]
  = domain accuracy for D+15..D+29

domain_change[domain]
  = second_half_accuracy - first_half_accuracy in percentage points

supplement_conversion
  = correct completed supplements / all completed supplements triggered by wrong base answers

review_accuracy
  = correct attempted due-review base questions / all attempted due-review base questions

not_yet_due
  = pending review items whose due_local_date is after D+30

average_duration_seconds
  = arithmetic mean of duration_seconds from first valid base submissions
```

Base and supplement counts use logical command keys, not Discord interaction IDs, so double-click replays do not inflate totals.

---

### Task 1: Pre-deployment fake-clock evaluation contract

**Files:**
- Test: `C:\Users\nasca\AppData\Local\hermes-plugins\cs-quiz\tests\test_evaluation.py`
- Test: `C:\Users\nasca\AppData\Local\hermes-plugins\cs-quiz\tests\test_integration.py`

**Interfaces:**
- Consumes: plugin implementation plan Task 6.
- Produces: deterministic proof that operational D+30 behavior is implemented before deployment.

- [ ] **Step 1: Verify D..D+30 boundaries with a fixed clock**

Use D=`2026-09-02` in fixtures. Assert creation allowed D..D+29, no creation D+30, and one evaluation object at D+30 20:00 Asia/Seoul. Include failed, expired, and incomplete sessions in the 30 calendar-day denominator.

- [ ] **Step 2: Verify every formula with hand-computable fixture data**

Create two domains with first/second-half attempts, wrong-base supplements, due and not-yet-due review items, and known durations. Assert exact fractions and percentage-point changes. Include zero-denominator domains and assert `None` in the domain report; presenter maps `None` to `측정 불가`.

- [ ] **Step 3: Verify D+30 idempotency and no expansion**

Call start/evaluation three times for D+30. Assert one report identity, no new session, no new question generation, and no language/math fields or actions.

- [ ] **Step 4: Run the pre-deployment evaluation tests**

```bash
cd C:/Users/nasca/AppData/Local/hermes-plugins/cs-quiz
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q tests/test_evaluation.py tests/test_integration.py
```

Expected: all pass before deployment approval.

---

### Task 2: Low-risk pilot monitoring on D through D+29

**Files:**
- Read only: cron run history, gateway logs, Discord quiz channel, plugin-state metadata.

**Interfaces:**
- Produces: incident ledger containing dates/status/error codes only; no answer text.

- [ ] **Step 1: Check each day only when there is an alert or explicit review request**

Read the exact cron job's most recent run, one session status, `render_pending`, and non-secret error code. Do not create a second monitoring cron for a one-user 30-day pilot.

- [ ] **Step 2: Classify failures without changing the calendar**

Use these exact labels:

```text
generation_failed
session_incomplete
session_expired
render_pending
cron_failed
no_issue
```

A retry may repair the day's session but never removes the day from the 30-day denominator.

- [ ] **Step 3: Preserve state on incidents**

Never reset or overwrite corrupt state. If primary JSON is unreadable, verify the previous-good backup as a read-only candidate and ask before any replacement. If interaction/render fails, keep `pilot_enabled` unchanged unless continuing risks duplicate state; pausing/disabling requires approval.

---

### Task 3: Verify the automatic D+30 run

**Files:**
- External read-only state: cron job, Discord channel, plugin state.

**Interfaces:**
- Consumes: exact `CRON_JOB_ID`, `QUIZ_CHANNEL_ID`, and D from deployment record.
- Produces: proof that one D+30 card exists and no D+30 quiz exists.

- [ ] **Step 1: Before D+30 20:00, read the expected next run**

Call `cronjob(action="list")` and verify the exact job ID is enabled with next fire at D+30 20:00 Asia/Seoul. Verify `pilot_enabled=true` and no D+30 session exists yet. Make no write.

- [ ] **Step 2: After the run, read cron outcome**

Read the exact job's latest completed run. Verify the plugin returned evaluation status, not generation status, and the run did not retry in the same tick.

- [ ] **Step 3: Read the Discord card**

Fetch the quiz channel message created/updated by the report. Verify it contains completion days, base/supplement totals, domain changes, supplement conversion, due-review performance, not-yet-due count, average duration, and next-course decision text. Verify no answer keys or raw answers appear.

- [ ] **Step 4: Verify no D+30 quiz state**

Use the read-only audit in Task 4. Assert there is no session whose `local_date == D+30`, and the evaluation report key exists exactly once.

---

### Task 4: Independently recompute the report without leaking answers

**Files:**
- Read only: active profile `plugin-data/*/state.json` selected by structure.
- Execute only: a temporary in-memory Python audit; do not write a report file containing state.

**Interfaces:**
- Consumes: `cs_quiz.engine.build_evaluation()` from the installed plugin commit.
- Produces: sanitized metric JSON with counts/rates/dates only.

- [ ] **Step 1: Locate the state file structurally**

Enumerate active profile `plugin-data/*/state.json` paths. Parse each privately and select the single file whose root contains key `quiz` and whose quiz state contains pilot/sessions/domain_stats. Print only the selected path. Zero or multiple matches is an error and stops the audit.

- [ ] **Step 2: Recompute with installed source code**

Run a Python process that loads the selected JSON, passes only `state["quiz"]` and D+30 to `build_evaluation()`, then constructs a sanitized dict containing:

```python
SAFE_KEYS = {
    "pilot_start",
    "pilot_end",
    "completion_days",
    "base_questions",
    "supplement_questions",
    "domain_accuracy",
    "domain_change",
    "supplement_conversion",
    "review_accuracy",
    "not_yet_due",
    "average_duration_seconds",
}
```

Before printing, assert every output key is in `SAFE_KEYS` and recursively reject keys containing `answer`, `feedback`, `rubric`, `prompt`, or `token`. Print the sanitized JSON only.

- [ ] **Step 3: Cross-check the Discord card**

Compare every displayed value to the sanitized audit. Rates must match using one decimal place; counts and dates must match exactly. Any mismatch is a defect, not “rounding noise,” unless the only difference is the documented one-decimal rendering.

- [ ] **Step 4: Verify record cardinality**

Programmatically assert:

```text
calendar days in window = 30
sessions per local_date <= 1
logical attempt keys unique
completion_days <= 30
base_questions <= 300
D+30 sessions = 0
evaluation reports = 1
```

A lower question count is allowed for failed/incomplete days; it must reconcile to session statuses.

---

### Task 5: Qualitative review and user decision

**Files:**
- No writes until the user chooses an action.

**Interfaces:**
- Produces: one user-owned decision: stop, revise/re-pilot, continue CS with a new approved window, or design a separate quiz family.

- [ ] **Step 1: Present evidence in priority order**

1. Completion and operational reliability.
2. Whether domain accuracy improved or regressed.
3. Whether supplements converted misconceptions.
4. Whether +1/+3/+7 review helped.
5. Time burden and interaction friction.
6. Content relevance to 청춘케어.

Separate confirmed metrics, interpretation, and unknowns.

- [ ] **Step 2: Ask the user four concrete questions**

```text
문제 난이도는 너무 쉬움 / 적절함 / 너무 어려움 중 어디였나?
해설과 자료 링크가 실제 이해에 도움이 됐나?
매일 10문제와 Discord 흐름이 지속 가능한 부담이었나?
다음 선택은 중단 / CS 수정 후 재시범 / CS 계속 / 별도 언어·수학 설계 중 무엇인가?
```

Do not infer satisfaction from completion rate alone.

- [ ] **Step 3: Record the decision without executing it**

Show the exact consequence and write operations for the selected option, then obtain a separate execution approval.

---

### Task 6: Approval-gated closeout actions

**Files:**
- External state only if approved.

**Interfaces:**
- Consumes: user's final decision.
- Produces: read-back-verified operational state; preserves pilot history.

- [ ] **Step 1: If stopping, propose pause then disable**

```text
cronjob(action="pause", job_id=CRON_JOB_ID)
hermes config set plugins.entries.cs-quiz.settings.pilot_enabled false
```

Execute only after approval, then list cron and read the setting. Keep plugin installed and state intact unless the user separately requests removal.

- [ ] **Step 2: If revising or continuing CS, start a new design cycle**

The existing D..D+29 window is immutable history. Do not reset dates or silently extend. Create a new bounded/architectural request defining new window, changed rules, state-version migration, and cron disposition; obtain design approval before code/config changes.

- [ ] **Step 3: If language or math is selected, create a separate spec**

Do not clone `cs-quiz` blindly. Re-run brainstorming for scope, domain mix, grading, resource catalog, state separation, channel, and schedule. No channel/job/plugin creation occurs from the evaluation decision alone.

- [ ] **Step 4: Verify final external state**

Read back exact cron enabled/paused state, `pilot_enabled`, plugin enabled state, channel existence, and preserved state byte size. Report what changed, why, and what deliberately remained.
