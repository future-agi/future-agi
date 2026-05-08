# Simulate App — Specification

The simulate app is the **AI agent testing and simulation platform**. It lets users run
scripted or persona-driven conversations against live agent endpoints, capturing transcripts,
metrics, and evaluation results. Supports both text (chat) and voice (phone) simulations.

---

## Architecture

```
POST /run_tests/{id}/execute/
    → RunTestExecutionView
    → TestExecution.create(status=PENDING)
    → start_test_execution_workflow() [Temporal]
    → TestExecutionWorkflow
        ├─ setup_test_execution() [activity]
        ├─ create_call_execution_records() [activity]
        ├─ CallExecutionWorkflow × N [child workflows]
        │   ├─ TEXT: ChatServiceManager → FutureAGIChatService / VapiChatService
        │   └─ VOICE: VAPI phone call via ee/voice/
        └─ finalize_test_execution() [activity]
```

---

## Core Models

### `RunTest`

The user-created test definition. Not an execution — a template that can be run multiple times.

| Field | Type | Notes |
|-------|------|-------|
| `name`, `description` | str | |
| `agent_definition` | FK → `AgentDefinition` | The agent under test |
| `agent_version` | FK → `AgentVersion`, nullable | Pinned version; `None` = latest active |
| `scenarios` | M2M → `Scenarios` | Which scenarios to run |
| `dataset_row_ids` | ArrayField(UUID) | Specific rows if dataset-based |
| `evaluations_config` | JSONField | Inline eval config |

### `TestExecution`

One run of a `RunTest`. Created when the user clicks "Execute".

| Field | Type | Notes |
|-------|------|-------|
| `run_test` | FK | |
| `status` | enum | `PENDING / RUNNING / EVALUATING / COMPLETED / FAILED / CANCELLED` |
| `scenario_ids` | JSONField | List of scenario UUIDs for this execution |
| `total_scenarios`, `completed_calls`, `failed_calls`, `analyzing_calls` | int | Progress counters |
| `agent_version` | FK, nullable | Pinned at execution time from `run_test.agent_version` |
| `eval_explanation_summary_status` | enum | `PENDING / RUNNING / COMPLETED / FAILED` — independent of main status |
| `picked_up_by_executor` | bool | Prevents double-execution if two workers see the same record |

### `CallExecution`

One simulated conversation (voice call or chat session). One row per (scenario, dataset_row).

| Field | Type | Notes |
|-------|------|-------|
| `test_execution` | FK | |
| `scenario` | FK → `Scenarios` | |
| `status` | enum | `PENDING / REGISTERED / ONGOING / COMPLETED / FAILED / ANALYSING / CANCELLED` |
| `simulation_call_type` | enum | `VOICE / TEXT` |
| `call_metadata` | JSONField | `{system_prompt, initial_message, chat_session_id, persona, …}` |
| `provider_call_data` | JSONField | `{provider_name: data}` — raw provider response |
| `eval_outputs` | JSONField | Final evaluation results |
| `row_id` | UUID, nullable | Dataset row this call was instantiated from |
| `agent_version` | FK, nullable | Version config used for this specific call |

**`call_metadata` keys:**
- `system_prompt` / `dynamic_prompt` / `base_prompt` — resolved in priority order (first non-null wins)
- `initial_message` — customer's opening line
- `chat_session_id` / `vapi_chat_session_id` — backward-compat fallback (see ADR 017)
- `persona` — persona dict if persona-based simulation

### `Scenarios`

A reusable test configuration. Three types, two source types.

| Field | Type | Notes |
|-------|------|-------|
| `scenario_type` | enum | `GRAPH / SCRIPT / DATASET` |
| `source_type` | enum | `AGENT_DEFINITION / PROMPT` |
| `agent_definition` | FK, nullable | |
| `simulator_agent` | FK → `SimulatorAgent` | The simulated customer |
| `dataset` | FK, nullable | For DATASET scenarios |

### `Persona`

Defines how the simulated customer behaves. All demographic/behavioral attributes are
**JSON arrays** — multiple values can be stored, but only the first element is used at
runtime (see ADR 016 and issue #311).

| Field | Type | Notes |
|-------|------|-------|
| `persona_type` | enum | `SYSTEM / WORKSPACE` |
| `persona_id` | int | Stable ID for system personas (1–8) |
| `simulation_type` | enum | `VOICE / TEXT` |
| Voice attributes | JSONField arrays | `accent`, `conversation_speed`, `background_sound`, `finished_speaking_sensitivity`, `interrupt_sensitivity` |
| Text attributes | JSONField arrays | `punctuation`, `emoji_usage`, `slang_usage`, `typos_frequency`, `tone`, `verbosity` |

**System personas** (8 hardcoded, created at boot via `insert_system_personas()`):
Impatient Driver, Lost Newbie, Stressed Accountant, Frustrated Subscriber,
Confused First-Time User, Curious Evaluator, No-Nonsense Executive, Frustrated Everyday User.

### `AgentVersion`

A point-in-time snapshot of an agent's configuration.

| Field | Type | Notes |
|-------|------|-------|
| `agent_definition` | FK | |
| `version_number` | int | |
| `configuration_snapshot` | JSONField | **Source of truth for call execution config** (see ADR 015) |
| `status` | enum | `ACTIVE / DRAFT / DEPRECATED` |

**Invariant:** `setup_test_execution()` loads `configuration_snapshot`, not the live
`agent_definition`. If no `agent_version` is pinned, it falls back to the latest ACTIVE
version silently (see issue #309).

### `SimulatorAgent`

The LLM-powered simulated customer.

| Field | Notes |
|-------|-------|
| `prompt` | System prompt for the simulated customer persona |
| `model`, `llm_temperature`, `max_tokens` | LLM config |
| `voice_provider`, `voice_name` | For voice simulations |
| `max_call_duration_in_minutes` | Hard stop |

---

## Execution Flow

### 1. Create RunTest

`POST /simulate/api/run_tests/`

Creates `RunTest` + `SimulateEvalConfig` rows. No execution starts.

### 2. Start Execution

`POST /simulate/api/run_tests/{run_test_id}/execute/`

**Request:** `{scenario_ids: [...], simulator_id: uuid | null}`

**Actions:**
1. Creates `TestExecution(status=PENDING, picked_up_by_executor=False)`
2. Calls `start_test_execution_workflow()` → starts `TestExecutionWorkflow` in Temporal
3. Returns `{execution_id, workflow_id, status: "started"}`

**Fallback:** If `TEMPORAL_TEST_EXECUTION_ENABLED=False`, falls through to deprecated
`TestExecutor.execute_test()` (Celery-based).

### 3. TestExecutionWorkflow (Temporal)

Lifecycle: `INITIALIZING → LAUNCHING → RUNNING → FINALIZING → COMPLETED | FAILED`

**INITIALIZING:**
- `setup_test_execution()` — loads scenarios, resolves `agent_version.configuration_snapshot`,
  enumerates dataset rows. Returns `SetupTestOutput`.
- `create_call_execution_records()` — creates one `CallExecution(status=PENDING)` per
  (scenario, row) pair. Substitutes dataset row values into system prompt.

**LAUNCHING:**
- Spawns `CallExecutionWorkflow` child workflows in batches of 50, sub-batches of 10
  with 5-second pauses to avoid overwhelming the agent.

**RUNNING:**
- Waits for `SIGNAL_CALL_COMPLETED` and `SIGNAL_CALL_ANALYZING` signals from children.
- Tracks `_completed_calls`, `_failed_calls`, `_analyzing_calls`.
- At 500 Temporal history events: checkpoints state via `continue_as_new()` (see ADR 018).

**FINALIZING:**
- `finalize_test_execution()` — writes final counts, sets `status=COMPLETED`,
  triggers async eval summary Celery task.

### 4. CallExecutionWorkflow (Child)

Per-call lifecycle: `PENDING → REGISTERED → ONGOING → ANALYSING → COMPLETED | FAILED`

**TEXT path:**
1. `ChatSimService.initiate_chat()` — creates simulator assistant + session, returns
   initial customer message.
2. Exchange messages until termination condition (max turns, goodbye phrase, timeout).
3. Mark `status=ANALYSING`, run evaluations.
4. Signal parent: `SIGNAL_CALL_COMPLETED`.

**VOICE path (EE):**
1. Acquire phone number slot.
2. Initiate VAPI call with persona voice settings.
3. Poll for call completion via webhook.
4. Mark `status=ANALYSING`, run evaluations.
5. Signal parent: `SIGNAL_CALL_COMPLETED`.

### 5. Rerun

`POST /simulate/api/test_executions/{id}/rerun/`

`{call_ids: [...], eval_only: bool}`

Launches `RerunCoordinatorWorkflow`. Supports merge: concurrent rerun requests for the
same `test_execution_id` are merged into one workflow via `MergeCallsSignal`.

`eval_only=True`: skips call execution, re-runs evaluations on existing transcripts.
`eval_only=False`: full re-execution of the call.

---

## Services

### `ChatServiceManager`

Provider-agnostic router. Delegates to `VapiChatService` or `FutureAGIChatService`.

| Method | Returns | Notes |
|--------|---------|-------|
| `create_assistant(name, system_prompt, voice_settings, model, temperature, max_tokens)` | `CreateAssistantResult` | |
| `delete_assistant(assistant_id)` | `DeleteAssistantResult` | |
| `create_session(assistant_id, name, initial_message)` | `CreateSessionResult` | |
| `send_message(session_id, messages)` | `SendMessageResult` | |

### `FutureAGIChatService`

Implements `ChatEngineBlueprint` using Django ORM + internal LLM client. State persisted
to `ChatSimulatorAssistant` / `ChatSimulatorSession` — recoverable across pod restarts.

### `ChatSimService.initiate_chat(call_execution, organization, workspace) → List[ChatMessage] | None`

**Contract:**
1. Check balance via `TestExecutor._check_call_balance()`. On failure: sets
   `call_execution.status=FAILED`, raises `ValueError`. Never returns `None` for balance failure.
2. Resolve system prompt: `call_metadata["system_prompt"]` → `"dynamic_prompt"` →
   `"base_prompt"` → `generate_simulator_agent_prompt()`.
3. Create assistant + session via `ChatServiceManager`.
4. Write `chat_session_id` to `call_metadata`, save.
5. Return initial messages from AI customer.

**Invariants:**
- `call_execution.status` is not advanced to `ONGOING` here — that happens in `CallExecutionWorkflow`.
- Side effect: `call_execution.call_metadata` is mutated and saved.

---

## Persona Resolution

At `CallExecution` creation time, persona data is copied into `call_metadata["persona"]`.

For **voice**: `Persona.to_voice_mapper_dict()` takes `field[0]` (first element of each
JSON array) and maps to VAPI voice settings.

For **text**: Persona attributes are injected into the simulator system prompt as
behavioural instructions (e.g., "use frequent typos", "speak in casual tone").

**Invariant:** Only the first element of each JSON array attribute is used at runtime,
even if multiple values are stored in the model. See issue #311.

---

## Dataset Row Substitution

When `Scenarios.scenario_type == DATASET`, `create_call_execution_records()` substitutes
dataset row values into the system prompt before storing in `call_metadata["system_prompt"]`.

**Known gap:** No validation that all placeholder tokens (e.g., `${email}`) were
replaced. Calls execute with literal placeholder text if the dataset row is missing a
column. See issue #312.

---

## Evaluation Pipeline

After each call reaches `ANALYSING` status:
1. `SimulateEvalConfig` rows for the `RunTest` are loaded.
2. Each eval is run via the evaluations engine (`run_eval()`).
3. Results written to `CallExecution.eval_outputs`.
4. After all calls complete, `finalize_test_execution()` triggers an async Celery task
   for eval explanation summary generation.

**`eval_explanation_summary_status`** tracks this separately from main execution status.
No timeout or retry if the Celery task silently fails. See issue #313.

---

## Known Design Issues

- **Agent version silent fallback** — ADR 015, issue #309
- **Legacy `TestExecutor` dual execution path** — ADR 014, issue #310
- **Persona multi-valued fields, only first used** — ADR 016, issue #311
- **No dataset row placeholder validation** — issue #312
- **Eval summary has no timeout/retry** — issue #313
- **Call status transitions not enforced** — any code can set any status; no state machine
- **`chat_session_id` / `vapi_chat_session_id` non-determinism** — ADR 017
