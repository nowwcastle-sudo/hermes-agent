# RFC: Discord Plugin Interactions and `cs-quiz`

**Status:** 설계 승인 완료 · 구현 및 운영 활성화 승인 전

**Date:** 2026-09-01

**Targets:** Hermes Agent core + 새 독립 `cs-quiz` 플러그인

**Pilot:** 첫 정상 운영일의 local date D부터 D+29까지 30 calendar days

## 1. 목적

강현성 한 명을 대상으로 Discord 안에서 매일 컴퓨터공학 퀴즈를 운영한다. 매일 Asia/Seoul 20:00에 한 문제씩 순차 제시하고, button 또는 Modal로 답을 받은 직후 채점·해설한다. 학습 기록, 오답, 보충 문제, 간격 반복은 transient Discord message와 분리해 영속 저장한다.

이 기능은 일반 cron 대화가 아니다. cron은 회차 시작만 담당하고, 답변 처리는 foreground Discord interaction과 독립 상태 머신이 담당한다.

이 문서에서 **host**는 Hermes core를 뜻한다. gateway와 Discord adapter는 host의 하위 구성요소다.

## 2. 문제 정의

현재 Hermes main은 일반 대화의 `ClarifyChoiceView`를 통해 객관식 button을 제공하지만, 독립 plugin이 Discord button·Modal callback을 등록하고 재시작 후 stable custom ID를 다시 route하는 공개 interface는 제공하지 않는다. cron job은 실행 중 사용자의 답을 기다릴 수 없다.

따라서 해결할 문제는 quiz prompt 작성이 아니라 다음 경로를 안전하게 연결하는 것이다.

1. 매일 20:00 회차 시작
2. Discord 네이티브 interaction
3. 결정론 채점과 제한된 LLM 채점
4. 재시작 가능한 영속 학습 상태
5. persist 이후 render 실패의 복구

## 3. 결정

Hermes core에 Discord 전용의 좁은 plugin seam을 추가하고, quiz domain은 새 `cs-quiz` plugin에 둔다.

- host는 interaction 등록·routing·ACK·Discord render/update만 안다.
- plugin은 문제 생성·채점·상태 전이·복습·표현 spec을 소유한다.
- cron은 plugin의 `cs_quiz_start` tool만 호출한다.
- 기존 `gosulgoseul-entertainer` plugin과 그 실험 코드는 사용·참조·이식하지 않는다.

## 4. 대안과 기각 이유

### 4.1 별도 Discord bot

새 bot token, process, 자동시작, log, LLM 인증을 별도로 운영해야 한다. 30일 1인 pilot에 비해 운영비가 크므로 기각한다.

### 4.2 Discord platform adapter 대체

기존 대형 adapter 기능을 복제·동기화해야 하고 같은 bot token으로 두 adapter를 병행할 수 없다. 변경 폭과 유지비가 과도해 기각한다.

### 4.3 기존 clarify + 일반 message 조합

Modal, 독립 callback, stable routing, 재시작 복구 요구를 충족하지 못하므로 비해결안이다.

## 5. 범위

### 5.1 포함

- 강현성 본인만 응답 가능한 Discord quiz
- 주말·공휴일 포함 매일 Asia/Seoul 20:00
- 기본 10문제: 객관식 6, 주관식 3, 서술형 1
- 7개 분야: SWE, Computer Science, Computer Structure, Network, DB, Algorithm, Data Structure
- 기본 10문제 중 7개는 분야별 1개, 나머지 3개는 약점·복습 slot
- 청춘케어 실무 상황, 분야 label, 해설, 용어 풀이, 교육 자료
- 오답 feedback 직후 `보충 문제 풀기` button 제공
- 다음날 20:00 직전까지 중단·재개
- 30일 기록과 D+30 평가
- quiz channel에서 Hermes mention 없는 일반 질문

“오답 직후”는 feedback card에 보충 button을 즉시 표시한다는 뜻이다. 보충 문제를 자동으로 덮어써서 feedback 읽기를 방해하지 않는다.

### 5.2 제외

- 별도 web UI
- 범플랫폼 interaction framework
- 별도 database
- 다중 사용자·역할 관리
- 문제은행 관리 화면
- 프로그래밍 언어 문법·framework 사용법 문제
- 기존 엔터테이너 plugin 재사용
- 30일 뒤 언어별·수학 quiz 자동 확장

## 6. Core interface

### 6.1 Versioned host types

공개 type은 `api_version=1`을 갖는다. Discord SDK 객체를 plugin에 노출하지 않는다.

```text
DiscordInteraction
- api_version: 1
- interaction_id
- kind: button | modal_submit
- plugin_id                 # host가 registration에서 주입
- action
- route_token
- user_id
- guild_id
- channel_id
- message_id
- component_value           # button value가 custom ID에 있었을 때 decode하여 복원
- modal_values              # modal_submit일 때 map
```

```text
DiscordComponentSpec
- type: button
- action                    # host가 custom ID에 encode
- route_token               # 16~43자의 opaque router; host가 custom ID에 encode
- label
- style: primary | secondary | success | danger
- value                     # optional non-empty UTF-8 opaque routing choice; custom ID에 encode
- disabled: bool
```

```text
DiscordModalSpec
- api_version: 1
- action                    # modal submit용 action
- title
- fields[]
  - id
  - label
  - style: short | paragraph
  - required: bool
  - min_length
  - max_length
```

```text
DiscordMessageSpec
- api_version: 1
- content
- embeds[]
- components: DiscordComponentSpec[]
```

```text
DiscordInteractionResult
- open_modal(DiscordModalSpec)
- update_message(DiscordMessageSpec)
- ephemeral(content)
- no_change
```

`open_modal`은 button interaction의 최초 응답에서만 허용한다. `open_`은 host가 예약한 generic action prefix다. 모든 `open_*` button callback은 defer하지 않고 2초 timeout 안에서 최초 응답을 만들어야 한다. `답변 입력` button은 action을 `open_short_answer` 또는 `open_essay`로 encode하므로 state I/O 없이 정해진 Modal을 만들 수 있다. Plugin author는 일반적인 deferred 작업에 `open_*` action을 사용하면 안 된다. `modal_submit`은 `open_modal`을 반환할 수 없다. Host는 schema에 없는 field와 잘못된 result-kind 조합을 거절한다.

### 6.2 Registration

```python
ctx.register_discord_interaction(
    handler=handle_interaction,
)
```

Namespace는 plugin 입력을 받지 않고 host가 `ctx.plugin_id`에서 파생한다.

보장:

- plugin unload/reload 때 registration 자동 해제
- plugin ID collision 거절
- gateway allowlist가 callback보다 먼저 적용
- 모든 `open_*` button callback은 2초, 그 밖의 deferred callback은 90초의 host timeout 적용
- callback timeout과 exception 격리
- capability를 registration과 매 dispatch 때 재검사

동기 callback은 `asyncio.to_thread`에서 실행한다. Timeout은 host가 worker를 기다리는 시간과 Discord 응답 latency만 제한한다. 이미 실행 중인 worker thread나 timeout 전 side effect를 강제 종료하거나 rollback할 수 없다. 따라서 trusted plugin은 host timeout보다 짧은 cooperative deadline을 callback과 I/O 경계에 전달·검사하고, durable transition을 idempotent하게 설계해야 한다. Commit 직전에는 deadline과 예상 revision을 다시 확인하여 deadline이 지난 결과나 revision mismatch를 거절하고 CAS로만 상태를 확정한다.

### 6.3 ACK policy

| Interaction | Host 선처리 | Plugin 작업 |
|---|---|---|
| 모든 `open_*` button | allowlist와 route shape만 검사, defer 없음, 2초 timeout | 아래 undeferred result/ACK matrix 중 하나를 즉시 반환 |
| 선택지·보충·다음 button | 즉시 defer | 상태 검증·전이·render result |
| modal submit | 즉시 defer | objective/essay 채점·render result |
| malformed·unauthorized | 즉시 ephemeral | callback 호출 안 함 |
| ACK `unknown interaction` 실패 | 중단 | 상태 변경 callback 호출 안 함 |

Undeferred `open_*` result/ACK matrix:

| Result | 정확한 최초 ACK |
|---|---|
| `open_modal` | `interaction.response.send_modal(...)` |
| `update_message` | `interaction.response.edit_message(**rendered_kwargs)` |
| `ephemeral` | `interaction.response.send_message(content, ephemeral=True)` |
| `no_change` | bounded 안내를 `interaction.response.send_message(..., ephemeral=True)` |

모든 interaction은 정확히 한 번 ACK된다. `open_*` 경로는 defer하지 않으며 initial response 뒤 `edit_original_response`나 follow-up을 추가로 보내지 않는다. Deferred `update_message`는 기존대로 `interaction.edit_original_response(**rendered_kwargs)`를 사용한다.

### 6.4 Send/update receipt

```python
receipt = await ctx.discord.send(channel_id=channel_id, spec=spec)
# {ok, channel_id, message_id, error_code}

result = await ctx.discord.update(
    channel_id=channel_id,
    message_id=message_id,
    spec=spec,
)
# {ok, error_code}
```

재시작 뒤 SDK cache가 비어 있어도 `channel_id + message_id`로 REST lookup/edit할 수 있어야 한다. Partial success는 `ok=false`와 stable `error_code`로 표현하며 exception text를 plugin에 노출하지 않는다.

### 6.5 Capability

Canonical capability registry에 `gateway.discord_interactions`와 consent 설명을 추가한다. `cs-quiz` manifest가 이를 선언하며 다음 시점에 재검사한다.

- registration
- dispatch
- send
- update

실행 중 revoke도 즉시 fail-closed 한다.

Capability는 sandbox가 아니다. 설치된 in-process plugin code는 신뢰된 code라는 threat model을 따른다. Capability의 목적은 사용자 동의와 accidental misuse 방지다.

### 6.6 Persistent routing

Message별 View 복원 목록을 저장하지 않는다. Custom ID wire format은 다음과 같고 전체 길이는 90자 이하로 제한한다.

```text
hdi1.<plugin_b64>.<action>.<route_token>[.<value_b64>]
```

`plugin_b64`와 optional `value_b64`는 padding 없는 canonical URL-safe base64이다. `value`가 없으면 기존 4-segment form을 그대로 생성·수용한다. `value`가 있으면 non-empty UTF-8 bytes를 optional 5번째 segment에 encode하고, interaction dispatch 시 이를 decode하여 `component_value`로 복원한다. Malformed, non-canonical, non-UTF-8 value segment는 callback 전에 거절한다. 이 value는 routing data이지 confidential data나 authorization secret가 아니다.

`session_id`는 `csq-YYYY-MM-DD`이고 `route_token`은 그 ID의 SHA-256 앞 24 hex 문자로 만든 stable opaque router다. Token은 authorization secret가 아니다. 실제 권한은 host allowlist와 plugin의 durable `guild_id + channel_id + message_id + route_token` 대조가 담당한다.

## 7. Atomic plugin state seam

현재 `ctx.state.get()`과 `set()`은 각각만 lock되므로 전체 read→transition→write transaction을 보장하지 않는다. Core에 generic CAS를 추가한다.

```python
committed = ctx.state.compare_and_set(
    key="quiz",
    expected=previous_snapshot,
    value=next_snapshot,
)
```

Host는 한 file lock 안에서 current JSON equality를 비교하고 atomic replace한다. Plugin callback을 lock 안에서 실행하지 않는다.

Plugin의 모든 전이는 다음 loop를 사용한다.

```text
read snapshot
→ pure transition
→ compare_and_set
→ CAS 실패 시 최신 snapshot으로 제한 횟수 재계산
```

LLM·Discord I/O 중에는 lock을 잡지 않는다. I/O 전에 durable claim을 CAS로 선점하고, 결과 반영 때 `session_id + question_id + command_key + revision`을 다시 확인한다.

## 8. Plugin architecture

Source repository:

```text
C:\Users\nasca\AppData\Local\hermes-plugins\cs-quiz
```

이 경로는 runtime discovery 대상이 아니다. 구현 후 지원되는 `hermes plugins install <path>` 동등 절차로 active profile에 설치하고 enable·gateway reload·discovery read-back을 검증한다.

Plugin이 외부에 노출하는 domain 진입점은 두 개뿐이다.

1. `cs_quiz_start` tool
2. `handle_interaction(interaction)` callback

내부 module:

- `QuizEngine`: start, submit, next, expire의 pure transition
- `Grader`: objective 채점과 essay rubric 채점
- `QuestionGenerator`: schema 기반 생성·검증
- `Presenter`: domain state를 Discord spec으로 변환
- `StateStore`: `ctx.state` CAS와 backup adapter

별도 DB는 만들지 않는다. `ctx.state`의 profile isolation, lock, atomic replacement, mode `0600`, 10 MiB quota를 사용한다.

Core `PluginState`는 `set()`과 `compare_and_set()`이 사용하는 같은 cross-process file lock 안에서 기존 정상 state 전체를 `_백업_원본_state.json` 한 세대로 atomic replace한 뒤 새 primary를 쓴다. `ctx.state.get_backup(key)`는 backup을 읽기 전용으로 노출한다. Plugin `StateStore`는 backup을 별도로 쓰지 않고 `get_backup("quiz")`만 검증해 복구 후보로 제시한다. State 원본이 파손되면 자동으로 빈 상태를 쓰거나 교체하지 않으며, 실제 원본 교체는 운영자 승인 뒤 수행한다.

## 9. Cron and start idempotency

Cron은 매일 Asia/Seoul 20:00에 `cs_quiz_start`를 호출한다. `local_date`가 start idempotency key다.

CAS transaction 안에서:

- 같은 날짜 `generating|active|completed`가 있으면 새 session을 만들지 않고 기존 상태를 반환한다.
- 같은 날짜 `failed`만 `generation_attempt`를 증가시켜 재시도한다.
- 전날 `active` 만료와 당일 `generating` claim 생성을 한 CAS로 commit한다.
- 만료되지 않은 generation lease가 있으면 중복 generator를 시작하지 않는다.
- 만료된 generation lease는 failed로 회수한 뒤 재시도할 수 있다.

Start flow:

1. `generating` claim과 lease를 CAS 저장
2. loading card 전송 후 receipt를 CAS 저장
3. lock 밖에서 문제 생성·검증
4. claim revision이 여전히 유효할 때만 `active`로 CAS 전환
5. 첫 문제 render

## 10. 상태 모델

`ctx.state` root key `quiz`:

```text
revision
pilot
├─ started_at
└─ ends_at
active_session_id
sessions
├─ <session_id>
domain_stats
└─ <domain>
review_queue[]
```

Owner user ID, guild ID, channel ID의 정본은 plugin settings다. Pilot state에 중복 저장하지 않는다.

### 10.1 QuizSession

```text
id                          # csq-YYYY-MM-DD
route_token                 # stable opaque router, authorization secret 아님
local_date
status: generating | active | completed | expired | failed
revision
started_at
expires_at
generation_attempt
generation_lease_until
generation_claim_id
generation_owner
current_question_index
current_question_presented_at
pending_supplement_id
loading_message: {channel_id, message_id} | null
quiz_message: {channel_id, message_id} | null
desired_view_revision
rendered_view_revision
render_pending
questions[]
attempts_by_command_key
processed_interaction_ids[]
grading_pending | null
```

불변식:

- active session은 최대 1개다.
- 기본 10문제를 모두 제출하고 발생한 보충 문제도 모두 완료해야 completed다.
- 보충 문제는 원문제 직후에만 등장한다.
- 보충 문제는 재귀 보충을 만들지 않는다.
- 같은 logical command는 최대 한 attempt만 확정한다.
- 다음날 20:00에 이전 active session은 expired다.

### 10.2 Question

```text
id
domain
topic
concept_key
learning_objective
reasoning_requirement: predict | diagnose | select_tradeoff | explain_cause
scenario_pattern
difficulty: basic | intermediate | advanced
type: multiple_choice | short_answer | essay
is_supplement: bool
scenario
prompt
choices[]
correct_choice_index
accepted_answers[]
rubric
explanation
wrong_answer_guidance
glossary[]
resource_keys[]
parent_question_id
```

정답과 rubric은 state에만 저장하고 Discord spec에는 포함하지 않는다.

### 10.3 Attempt and logical command

```text
command_key = session_id + question_id + phase
interaction_id
question_id
submitted_at
raw_answer
answer_hash
normalized_answer
correctness
score
rubric_scores
feedback
is_supplement
duration_seconds
```

`phase`는 `answer`, `supplement_answer`, `next` 중 하나다. 서로 다른 Discord interaction ID의 double click도 같은 command key면 저장된 pending/result를 replay한다.

Essay는 LLM 호출 전에 다음 claim을 CAS로 저장한다.

```text
grading_pending
- command_key
- answer_hash
- lease_until
- revision
```

Lease가 유효하면 두 번째 요청은 채점 중 상태를 replay한다. 결과는 claim이 여전히 일치할 때만 한 번 확정한다.

`duration_seconds`는 해당 문제의 render 성공 시각 `current_question_presented_at`부터 첫 유효 제출까지다.

### 10.4 ReviewQueueItem

```text
concept_key
source_question_id
due_local_date
interval_days: 1 | 3 | 7
status: pending | completed | missed
```

기본 문제 오답 확정 시 +1/+3/+7 세 항목을 생성한다. 같은 source question과 interval의 중복 항목은 만들지 않는다.

## 11. 채점

### 11.1 객관식

선택지 index와 정답 index를 정수 비교한다.

### 11.2 주관식

제출 답과 `accepted_answers` 양쪽에 다음 정규화를 적용한 뒤 완전 일치시킨다.

1. Unicode NFKC
2. 앞뒤 공백 제거
3. 연속 공백 축소
4. 영문 casefold

Validator는 정규화 후 빈 값과 중복을 거절한다. Fuzzy matching은 사용하지 않는다.

### 11.3 서술형

고정 10점 rubric:

- 개념 정확성 0–4
- 원인·과정 설명 0–3
- 청춘케어 상황 적용 0–2
- 표준 전문용어 연결 0–1

0–5점은 오답으로 판정해 보충 문제를 제공하고, 6–10점은 정답이다. 일일 기본 점수에는 실제 0–10점을 반영한다.

LLM schema:

```text
score
criterion_scores
correct_points[]
missing_points[]
misconceptions[]
terminology_corrections[]
feedback
```

결정론 validator가 정확한 criterion key, 각 범위, `score == sum(criterion_scores)`, 총점 0–10, 문자열·배열 길이를 검사한다. Answer는 data delimiter 안에 넣고 system instruction으로 취급하지 않는다. Schema·semantic validation 실패나 timeout이면 attempt를 확정하지 않고 재제출을 허용한다.

객관식·주관식에는 LLM fallback을 사용하지 않는다.

## 12. Discord interaction flow

### 12.1 문제 표시

객관식은 네 선택지 button, 주관식·서술형은 `답변 입력` button을 사용한다.

### 12.2 제출

```text
interaction
→ host allowlist와 ACK
→ plugin owner/channel/session/command 검증
→ claim 또는 채점
→ CAS persist
→ Discord feedback render
```

### 12.3 Feedback and supplement

모든 답에 정오답·rubric 점수, 개념 설명, 처음 나온 전문용어, 한국어 우선 교육 자료를 제공한다.

오답이면 feedback card에 `보충 문제 풀기`를 즉시 표시한다. 보충을 완료한 뒤 `다음 문제`가 나타난다. 보충은 객관식 또는 주관식이며, 보충 오답에는 해설만 제공하고 추가 보충은 없다.

### 12.4 Next

자동 진행하지 않는다. 사용자가 feedback을 읽고 `다음 문제`를 클릭해야 같은 message가 다음 기본 문제로 갱신된다.

### 12.5 Render reconciliation

Domain state가 정본이다.

- State 전이 때 `desired_view_revision`을 증가시키고 `render_pending=true`로 저장한다.
- Plugin은 deferred callback 안에서 `ctx.discord.update(channel_id, message_id, spec)`를 직접 호출해 receipt를 받는다. `ok=true` 뒤에만 `rendered_view_revision=desired_view_revision`, `render_pending=false`로 CAS 저장하고 callback은 `no_change`를 반환한다.
- Update error receipt면 `render_pending=true`를 유지하고 bounded ephemeral 안내를 반환한다. Host가 plugin 반환 뒤 대신 edit하는 `update_message` directive는 durable render acknowledgement에 사용하지 않는다.
- 정상 stale/중복 interaction은 오류만 반환하지 않고 현재 durable state를 다시 render한다.
- 변조 token, 잘못된 owner/channel/message는 fail-closed 한다.
- Plugin 등록, gateway 시작, `cs_quiz_start` 때 pending render를 reconciliation한다.

### 12.6 Resume and mention-free channel

Gateway 재시작 후 stable custom ID가 현재 state로 route된다. Quiz channel은 `free_response_channels`에만 추가하여 Hermes mention 없는 일반 질문을 허용한다. 다른 channel 설정은 변경하지 않으며 component 채점은 일반 message parsing에 의존하지 않는다.

### 12.7 Complete

기본 10문제와 발생한 보충 문제를 끝내면 기본 점수, 분야별 결과, 오답→보충 전환, 오늘의 핵심 3개, 다음 복습 예정 card를 표시하고 component를 비활성화한다.

## 13. 문제 생성

### 13.1 Code-owned slots

Plugin code가 다음을 먼저 결정한다.

- 7개 분야 기본 slot
- 기본 10문제 중 약점·복습 slot 3개
- 유형 6/3/1
- 분야별 난이도
- 복습 concept
- 언어 문법·framework 문제 금지

Pilot vocabulary는 7개 분야별 4개 language-agnostic concept, 총 28개로 고정한다. Scenario pattern은 `duplicate_submission`, `stale_ui_after_restart`, `concurrent_update`, `latency_timeout`, `data_validation`, `permission_scope`, `schedule_boundary`, `partial_failure`, `cache_miss`, `audit_reconciliation`, `resource_limit`, `schema_migration`의 12개다. 28×12=336조합이므로 pilot 기본 300문제에서 `concept_key + scenario_pattern`을 반복하지 않는다.

LLM은 이 slot 안에서 scenario와 문항을 작성한다.

### 13.2 Concept-understanding quality

모든 문제는 `learning_objective`와 reasoning requirement를 가져야 한다. Definition recall만 요구하는 prompt는 거절한다. 적어도 다음 중 하나를 요구해야 한다.

- 결과 예측
- 원인 진단
- trade-off 선택
- 인과 과정 설명

Fixture validator test는 순수 암기형 문항을 reject하고 청춘케어 scenario에 개념을 적용한 문항을 accept해야 한다.

### 13.3 생성 단위와 budget

기본 10문제와 각 문제의 숨은 보충 10개를 한 번에 생성한다. 이는 feedback 뒤 보충 button 클릭부터 card 표시까지 plugin 처리 2초 이내를 목표로 하기 위한 승인된 trade-off다.

내부 보수 limit:

- scenario 500자
- prompt 800자
- 선택지 각 200자
- explanation 1,500자
- glossary 항목 300자, 최대 5개
- wrong guidance 1,000자
- Discord card 총 text 4,000자
- component label 70자
- custom ID 90자
- 주관식 input 300자
- 서술형 input 2,000자
- 1 session serialized JSON 250 KiB 이하
- 30일 전체 state 8 MiB 이하

Presenter가 의미를 바꾸도록 잘라내지 않는다. Limit 초과 문제는 생성 단계에서 reject/regenerate한다.

### 13.4 Validator

- 기본 문제 정확히 10개
- 객관식 6, 주관식 3, 서술형 1
- 7개 분야 최소 1개
- 선택지 4개, 정규화 후 중복 없음, 정답 index 0–3
- 주관식 허용 답안 1개 이상
- 서술형 rubric 존재
- 모든 문제에 `resource_keys` 1개 이상
- 모든 보충에 parent question ID와 `is_supplement=true`
- 보충 type은 객관식 또는 주관식
- 최근 30일 동일 `concept_key + scenario_pattern` 없음
- 언어 문법·framework 문제가 아님
- 실제 개인정보 없음
- §13.3 size budget 충족

실패하면 validator 오류를 LLM에 주고 한 번만 재생성한다. 두 번째도 실패하면 failed로 남기고 부분 문제를 게시하지 않는다.

## 14. 교육 자료

LLM은 URL이나 최종 resource key를 결정하지 않는다. LLM이 생성한 `concept_key`를 plugin resolver가 `resource_catalog.json`에 매핑한다.

```text
resource_key
title
language
url
domains[]
concept_keys[]
authority
last_checked_at
reviewed_by
```

선택 규칙:

1. 같은 concept의 유효한 한국어 항목이 있으면 한국어를 선택한다.
2. 한국어 항목이 없을 때만 영어 공식·표준 항목을 선택한다.
3. 개인 blog는 catalog에 넣지 않는다.

Catalog 편입은 URL 접근, 문서 제목, 해당 concept의 실제 설명 존재를 사람이 검토해 `reviewed_by`와 `last_checked_at`을 기록한다. 자동 test는 한국어 후보가 있을 때 영어 후보를 선택하지 않는지 검증한다.

## 15. 난이도와 복습

### 15.1 Day 1–7

매일 기본 10 slot 중 basic 6, intermediate 4, advanced 0이다. 같은 분야의 여러 slot이 있으면 적응형 slot에 intermediate를 우선 배정한다.

### 15.2 Day 8 이후

분야별 최근 기본 문제 표본이 3개 미만이면 basic을 선택한다. 표본이 3개 이상이면:

- 0–59%: basic
- 60–84%: intermediate
- 85–100%: 해당 분야 출제 cycle에서 intermediate, intermediate, advanced 순환

보충 정답은 난이도 상승 정답률에 넣지 않는다.

### 15.3 기본 10문제 중 복습·약점 slot 3개

선택 순서:

1. 오늘 due인 +1/+3/+7 오답 concept
2. 정답률이 낮은 분야
3. 오래 출제되지 않은 concept

### 15.4 중복 방지

최근 30일의 concept key, scenario pattern, prompt hash를 저장한다. Prompt hash 완전 중복은 거절한다. 같은 concept은 복습이면 허용하지만 같은 concept + 같은 scenario pattern은 거절한다.

## 16. 30일 경계

Pilot은 시작 local date D부터 D+29까지 30 calendar days다. 생성 실패·미완료·만료일도 기간에 포함한다. D+30 20:00 cron run은 신규 quiz를 만들지 않고 평가 card만 게시한다.

평가 항목:

- 완료일수 / 30
- 총 기본·보충 문항
- 분야별 정답률 변화
- 오답→보충 정답 전환율
- 평가 시점까지 due가 된 +1/+3/+7 복습 성과
- 아직 due가 되지 않은 복습 항목 수
- 평균 소요시간
- 다음 과정 후보

자동 확장은 하지 않는다.

## 17. 오류 처리

상태 변경은 외부 message edit보다 먼저 확정한다.

```text
validate → claim/grade → CAS persist → render → render CAS acknowledge
```

| 오류 | 사용자 표시 | 상태 |
|---|---|---|
| 생성 1차 schema 실패 | 없음, 자동 재시도 | generating |
| 생성 2차 실패 | 오늘 문제 생성 실패 | failed |
| essay LLM 실패 | 채점 일시 실패, 다시 제출 | attempt 미확정·claim 만료 후 회수 |
| Discord edit 실패 | 같은 component를 다시 누르면 저장된 현재 화면 복원 | render_pending 유지 |
| 정상 stale UI | 저장된 현재 화면 재표시 | 변경 없음 |
| 변조 ID | 유효하지 않은 문제 | 변경 없음 |
| 다른 사용자 | 허용된 학습자가 아님 | 변경 없음 |
| 만료·완료 ID | 현재 상태 안내 | 변경 없음 |
| state JSON 파손 | 학습 상태를 읽을 수 없음 | 빈 초기화 금지·backup 후보 보고 |
| handler 예외 | 일반 오류 + 추적 ID | traceback은 log에만 |

## 18. 보안

- Owner, guild, channel의 정본은 plugin settings다.
- Discord token과 LLM credential은 host가 소유한다.
- Capability는 trusted plugin consent gate이며 sandbox가 아니다.
- Namespace는 host가 plugin ID에서 파생한다.
- Callback마다 owner, guild, channel, message, session, logical command를 재검증한다.
- Message·Modal input은 data이며 명령으로 실행하지 않는다.
- LLM 결과는 untrusted data이며 schema·semantic·catalog validator 전에는 저장하지 않는다.
- 기본 log에는 answer 전문을 남기지 않는다.

## 19. 테스트 계약

### 19.1 Core

- registration, dispose, reload, collision
- capability 등록·거절·실행 중 revoke
- malformed/forged custom ID
- callback dispatch, timeout, exception isolation
- ACK exactly-once, unknown interaction, open-modal 예외
- send receipt와 cache 없는 REST update
- `PluginState.compare_and_set` thread/process contention

### 19.2 Plugin

- 6/3/1, 7분야, concept-quality, size validator
- objective grader와 accepted-answer 양쪽 정규화
- essay schema·semantic invariant
- logical command double click: 동일·서로 다른 interaction ID
- essay lease, 동시 LLM 호출 방지, lease recovery
- 오답→보충→다음, 보충 재귀 금지
- start local-date idempotency와 generation lease recovery
- persist/render crash injection과 reconciliation
- Day 1–7, Day 8+ exact difficulty
- +1/+3/+7 queue
- 한국어 resource 우선 resolver
- D부터 D+30 fake clock
- state budget와 backup corruption recovery

### 19.3 Integration

```text
start tool
→ 첫 card
→ 객관식 오답
→ 보충
→ 다음 문제
→ 주관식 Modal
→ 서술형 Modal
→ gateway reload
→ cache 없는 기존 custom ID 재개
→ 완료
```

Cron worker와 gateway event loop의 실제 동시 CAS, essay 채점 중 만료, 모든 persist/render crash 경계를 포함한다. LLM은 fixture JSON으로 고정한다.

### 19.4 Regression

Baseline commit `b6bcb3e791c673e63974029bbab40cc9326803ff`에서 다음 세 file의 전체 41 tests가 통과했다.

- `tests/gateway/test_discord_clarify_buttons.py`
- `tests/gateway/test_discord_component_auth.py`
- `tests/gateway/test_discord_platform_events.py`

Core 검증 명령:

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/gateway/test_discord_component_auth.py \
  tests/gateway/test_discord_platform_events.py
```

Plugin 검증 명령은 새 repository root에서 다음을 사용한다.

```bash
uv run --no-project --python 3.11 --with-editable . --with pytest --with pytest-asyncio pytest -q
```

## 20. 독립 구현 tranche

한 번에 섞지 않고 다음 네 tranche를 각각 검증·승인한다.

1. **Core interaction seam**
   - host types, ACK router, capability, send/update, CAS
   - live Discord나 quiz domain 없이 fake callback으로 검증
2. **`cs-quiz` plugin**
   - state machine, generator, grader, presenter, catalog
   - fixture host와 fixture LLM으로 검증
3. **Discord·cron deployment**
   - plugin install/enable, capability grant, channel, mention-free, Day 0 smoke, cron
   - 모든 external write는 직전 승인과 read-back 필요
4. **30일 evaluation**
   - D+30 report와 다음 과정 판단
   - 자동 확장 없음

## 21. 출시 절차와 승인 경계

1. Source repository를 active profile plugin으로 설치한다.
2. `hermes plugins list/info/doctor` 동등 read-back으로 discovery와 registration을 확인한다.
3. `pilot_enabled=false`로 유지한다.
4. 전용 channel을 만들고 owner/bot 중심 권한을 검토한다.
5. Quiz channel만 mention-free로 설정한다.
6. `pilot_enabled=false`인 상태에서 Cron을 만들고 즉시 pause한 뒤 name, schedule, timezone, delivery, toolset, paused state를 read-back한다.
7. 사용자가 pilot 시작 local date D, `pilot_enabled=true`, exact cron resume와 manual run을 최종 승인한다.
8. Pilot을 enable하고 cron을 resume한 뒤 exact job을 한 번 manual run한다. 이 첫 실제 session이 D이자 live smoke다.
9. Live smoke가 통과한 경우에만 cron을 enabled 상태로 유지한다. 실패하면 승인 뒤 cron pause → pilot disable 순서로 멈추고 state는 보존한다.

Channel·permission 생성, config 변경, capability grant, cron 생성·활성화, pilot 활성화는 외부 write다. 구현 완료가 운영 활성화 승인을 대신하지 않는다.

## 22. 완료 기준

- 새 regression test가 production change 전 red다.
- Core와 plugin unit/integration test가 green이다.
- Baseline Discord tests가 green이다.
- `git diff --check`가 통과한다.
- Staged credential scan은 secret 값을 출력하지 않고 file path만 검사하며 match 0건이다.
- Core diff는 승인된 host seam·test·RFC만 포함한다.
- Plugin diff는 `cs-quiz` repository만 포함한다.
- Live smoke에서 본인 button, 주관식 Modal, 서술형 Modal, unauthorized, double click, restart resume, complete disable을 검증한다.
- Plugin discovery, capability/settings, cron schedule/delivery/enabled를 read-back한다.
- 사용자 최종 승인 전 `pilot_enabled=false`다.

## 23. 구현 원칙

- 작은 diff와 기존 code style을 우선한다.
- Core에는 quiz domain type을 넣지 않는다.
- Discord 하나를 위해 범플랫폼 abstraction을 만들지 않는다.
- 새 dependency보다 Python stdlib와 기존 Hermes module을 사용한다.
- Objective 채점의 정확성을 편의보다 우선한다.
- Test를 고쳐 통과시키지 않고 production behavior를 고친다.
- 기존 엔터테이너 plugin은 source·dependency·migration input이 아니다.
