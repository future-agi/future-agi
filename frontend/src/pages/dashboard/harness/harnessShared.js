import { STATUS_TYPES } from "src/utils/statusUtils";

// The stage strings we branch on by name. "completed" is the successful outcome;
// "failed"/"canceled" are the terminal-failure outcomes (they are not members of
// `stages`, which only holds pipeline positions). Kept as one enum so callers
// stop hardcoding these literals.
export const HARNESS_STAGE = {
  QUEUED: "queued",
  RUNNING: "running",
  FINALIZING: "finalizing",
  CLEANING_UP: "cleaning_up",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELED: "canceled",
};

export const terminalStages = new Set([
  HARNESS_STAGE.COMPLETED,
  HARNESS_STAGE.FAILED,
  HARNESS_STAGE.CANCELED,
]);

// The ordered pipeline, by when the runner reaches each stage rather than by the declaration
// order of HarnessStage in the ALK wheel (fi/alk/harness/job.py): validating_environment runs
// after the scenarios exist, because it proves their reference solutions. In enum order the
// checklist rewinds. Same divergence as cleaning_up and uploading_artifacts below.
// "failed" and "canceled" are outcomes rather than positions, so they stay out: a stage
// missing from this list indexes to -1, which strands the checklist showing nothing reached
// and pins the progress bar at its 2% floor.
export const stages = [
  "queued",
  "acquiring_source",
  "understanding_agent",
  "generating_environment",
  "building_environment",
  "generating_data",
  "generating_scenarios",
  "validating_environment",
  "validating_scenarios",
  "connecting_agent",
  "running",
  "grading",
  "uploading_artifacts",
  "cleaning_up",
  "completed",
];

// ALK reports events against a coarser vocabulary than the checklist shows: one "environment"
// event covers four UI stages. Six stages (building/validating environment, generating data,
// validating scenarios, connecting agent, grading) are never emitted at all, so they can only
// be credited through the group they belong to.
// `validating_environment` is grouped with scenarios, not environment: its failures name
// scenarios, and grouping it under environment sends the working tab backwards mid-run.
const EVENT_STAGE_GROUPS = {
  understand: ["understanding_agent"],
  environment: [
    "generating_environment",
    "building_environment",
    "generating_data",
  ],
  scenarios: [
    "generating_scenarios",
    "validating_environment",
    "validating_scenarios",
  ],
  calls: ["connecting_agent", "running", "grading"],
  cleaning_up: ["cleaning_up"],
  uploading_artifacts: ["uploading_artifacts"],
  completed: ["completed"],
};

// Stages that always precede any emitted event: reaching any stage at all means the job was
// queued and its source acquired, though neither is ever reported.
const IMPLIED_STAGES = ["queued", "acquiring_source"];

// A cancel names no stage, unlike a failure, so reconstruct how far the run got from its
// events. Credit is given only for groups actually observed completing — never inferred from
// a stage's position in the list, because the runner does not emit in list order (it emits
// cleaning_up before uploading_artifacts, the reverse of the declared enum).
export const canceledProgress = (events = []) => {
  const done = new Set();
  const completedGroups = new Set();
  let lastStarted = null;
  let sawAny = false;

  events.forEach((event) => {
    const group = event?.payload?.stage;
    if (!EVENT_STAGE_GROUPS[group]) return;
    sawAny = true;
    if (event?.type?.endsWith(".completed")) {
      completedGroups.add(group);
    } else if (event?.type?.endsWith(".started")) {
      lastStarted = group;
    }
  });

  if (!sawAny) return { doneStages: done, stoppedAt: -1 };

  IMPLIED_STAGES.forEach((stage) => done.add(stage));
  completedGroups.forEach((group) => {
    EVENT_STAGE_GROUPS[group].forEach((stage) => done.add(stage));
  });

  // The run stopped inside the last group that started and never reported completing.
  const stalled =
    lastStarted && !completedGroups.has(lastStarted) ? lastStarted : null;
  const stoppedAt = stalled
    ? stages.indexOf(EVENT_STAGE_GROUPS[stalled][0])
    : -1;
  if (stoppedAt >= 0) done.delete(stages[stoppedAt]);

  return { doneStages: done, stoppedAt };
};

// How many stages the run has finished. The current stage is not one of them, so this is the
// index — and a completed run has finished all of them.
export const completedStageCount = (status, events = []) => {
  if (status?.stage === "completed") return stages.length;
  // "failed" and "canceled" are not members of the stage list, so indexing on them reports
  // nothing finished — even though the stepper knows better. Use the same anchors it does.
  if (status?.stage === "failed") {
    const failedIndex = stages.indexOf(status?.failure?.stage);
    return failedIndex < 0 ? 0 : failedIndex;
  }
  if (status?.stage === "canceled") {
    const { stoppedAt } = canceledProgress(events);
    return stoppedAt < 0 ? 0 : stoppedAt;
  }
  const index = stages.indexOf(status?.stage);
  return index < 0 ? 0 : index;
};

// Authoring writes `metadata.name`, older jobs `metadata.agent_name`. Never fall back to the
// job id: it reads as a name and search cannot match it. Callers whose slot cannot be blank
// pass their own fallback.
export const environmentName = (job, fallback = "\u2014") => {
  const metadata = job?.metadata || {};
  if (metadata.agent_name) return metadata.agent_name;
  if (metadata.name) return metadata.name;
  // Fall back to the source repo name (always present for github sources) so
  // environments submitted without an explicit metadata.name — older jobs, API
  // callers — read as a human name instead of a bare em-dash, per #2427's intent.
  const repository = job?.source?.repository;
  if (repository) {
    const repoName = String(repository).split("/").filter(Boolean).pop();
    if (repoName) return repoName;
  }
  return fallback;
};

// Four visual states for the run checklist.
export const STAGE_STATE = {
  DONE: "done",
  ACTIVE: "active",
  FAILED: "failed",
  STOPPED: "stopped",
  PENDING: "pending",
};

// `status.stage` holds the terminal outcome once a run ends, and "failed"/"canceled" are not
// members of `stages` — so indexing on it strands every row at -1 and blanks the checklist.
// A failed run instead names the stage it failed in via `failure.stage`, which is in the same
// vocabulary as `stages`, so that is what anchors the completed/errored split.
//
// When there is nothing to anchor on (a cancel, or a failure stage outside the list) every row
// stays pending: marking stages complete without evidence would invent progress that may not
// have happened.
export const stageState = (status, index, events = []) => {
  const stage = status?.stage;
  if (stage === "completed") return STAGE_STATE.DONE;

  if (stage === "failed") {
    const failedIndex = stages.indexOf(status?.failure?.stage);
    if (failedIndex < 0) return STAGE_STATE.PENDING;
    if (index < failedIndex) return STAGE_STATE.DONE;
    if (index === failedIndex) return STAGE_STATE.FAILED;
    return STAGE_STATE.PENDING;
  }

  if (stage === "canceled") {
    const { doneStages, stoppedAt } = canceledProgress(events);
    if (index === stoppedAt) return STAGE_STATE.STOPPED;
    if (doneStages.has(stages[index])) return STAGE_STATE.DONE;
    return STAGE_STATE.PENDING;
  }

  const currentIndex = stages.indexOf(stage);
  if (currentIndex < 0) return STAGE_STATE.PENDING;
  if (index < currentIndex) return STAGE_STATE.DONE;
  // The stage the runner is in is in progress, not finished. Ticking it green here is what
  // made a running job read as though it had already completed the step.
  if (index === currentIndex) return STAGE_STATE.ACTIVE;
  return STAGE_STATE.PENDING;
};

// Stage names as a person would say them. `readable` de-snake-cases anything not listed,
// which is right for field names but produces "Running" and "Grading" for stages that are
// really "Running scenarios" and "Grading results".
const STAGE_LABELS = {
  acquiring_source: "Preparing source",
  running: "Running scenarios",
  grading: "Grading results",
};

export const readable = (value = "") =>
  STAGE_LABELS[value] ||
  value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
export const displayText = (value, fallback = "") => {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return fallback;
  }
};

export const providerCredentialName = (connector) =>
  connector === "vapi"
    ? "VAPI_API_KEY"
    : ["retell", "retell_chat"].includes(connector)
      ? "RETELL_API_KEY"
      : null;

export const parseProviderDynamicVariables = (text) => {
  if (!text.trim()) return {};
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("Dynamic variables must be valid JSON.");
  }
  if (
    !value ||
    Array.isArray(value) ||
    typeof value !== "object" ||
    Object.entries(value).some(
      ([name, item]) =>
        !name || !["string", "number", "boolean"].includes(typeof item),
    )
  ) {
    throw new Error(
      "Dynamic variables must be a JSON object with string, number, or boolean values.",
    );
  }
  return value;
};
// "sha256:<64 hex>" reads as noise in a label; the first 12 hex digits identify it on screen.
export const shortDigest = (digest) => {
  const text = String(digest || "");
  const hex = text.startsWith("sha256:") ? text.slice(7) : text;
  return hex ? hex.slice(0, 12) : "";
};

// The adaptation's steps (environment-releases.md §7.5), each done once the builder's tool for it
// succeeded. A backend may prefix a tool with its server (`mcp__adaptation__run_proof`).
export const ADAPTATION_STEPS = [
  { key: "binding", label: "Binding drafted", tool: "submit_binding" },
  { key: "proof", label: "Binding proved", tool: "run_proof" },
  {
    key: "conflicts",
    label: "Policy conflicts reviewed",
    tool: "report_conflicts",
  },
  { key: "sealed", label: "Approved and sealed", tool: "finish_adaptation" },
];

// Which steps are done, from the conversation's tool results in order. A new binding voids the
// proof of the one before it, so an earlier proof never shows a later binding as proved.
export const adaptationProgress = (conversation) => {
  const done = new Set();
  for (const event of conversation?.events || []) {
    if (event?.kind !== "tool_result" || event?.payload?.is_error) continue;
    const tool = String(event?.payload?.tool || "");
    const step = ADAPTATION_STEPS.find(
      (one) => tool === one.tool || tool.endsWith(`__${one.tool}`),
    );
    if (!step) continue;
    if (step.key === "binding") done.delete("proof");
    done.add(step.key);
  }
  return done;
};

// A design's steps (environment-releases.md §7.9), each done once the builder's tool for it
// succeeded. Building starts only after the person approves the proposal, so the first build
// tool to succeed marks the design approved.
export const DESIGN_STEPS = [
  {
    key: "approved",
    label: "Design approved",
    tools: [
      "create_schema",
      "define_tool",
      "add_policy_rule",
      "import_rows",
      "save_world",
    ],
  },
  {
    key: "world",
    label: "World, tools and rules built",
    tools: ["save_world"],
  },
  {
    key: "suite",
    label: "Scenarios written and proved",
    tools: ["check_design"],
  },
  {
    key: "sealed",
    label: "Sealed as your environment",
    tools: ["finish_design"],
  },
];

// The failure codes of a design that can be continued from its checkpoint.
export const CONTINUABLE_DESIGN_CODES = ["design_waiting", "design_unfinished"];

export const designProgress = (conversation) => {
  const done = new Set();
  for (const event of conversation?.events || []) {
    if (event?.kind !== "tool_result" || event?.payload?.is_error) continue;
    const tool = String(event?.payload?.tool || "");
    for (const step of DESIGN_STEPS) {
      if (step.tools.some((one) => tool === one || tool.endsWith(`__${one}`))) {
        done.add(step.key);
      }
    }
  }
  return done;
};

// The request body adaptation and composition share (environment-releases.md §7.6). The
// agent block is the hosted-job agent in provider_import mode, so each run clones the agent.
export const releaseUsePayload = ({
  suiteId,
  agentVersionId,
  name,
  connector,
  targetId,
  dynamicVariables,
  credentialValues,
  idempotencyKey,
}) => ({
  suite_id: suiteId,
  credential_values: credentialValues,
  agent_version_id: agentVersionId,
  name: name.trim(),
  idempotency_key: idempotencyKey,
  agent: {
    connector,
    mode: "provider_import",
    config: {
      ...(connector === "vapi"
        ? { assistant_id: targetId.trim() }
        : { agent_id: targetId.trim() }),
      ...(connector === "retell_chat" && dynamicVariables.trim()
        ? { dynamic_variables: parseProviderDynamicVariables(dynamicVariables) }
        : {}),
    },
  },
});

export const BINDING_MODE_LABELS = {
  canonical: "Canonical",
  tool_routed: "Tool-routed",
  conversation_only: "Conversation only",
};

export const SCENARIO_ORIGIN_LABELS = {
  base: "Base",
  specialized: "Specialized",
  custom: "Custom",
};

// Adaptation questions carry `{value, label}` options; older builder questions carry strings.
export const questionOptions = (payload) =>
  (Array.isArray(payload?.options) ? payload.options : []).map((option) =>
    option && typeof option === "object"
      ? {
          value: option.value,
          label: displayText(option.label ?? option.value),
        }
      : { value: option, label: displayText(option) },
  );

// Message ids the user already answered: a question or proposal is settled once a user turn
// replies to it, so its buttons must not offer a second answer.
export const answeredMessageIds = (messages = []) =>
  new Set(
    (Array.isArray(messages) ? messages : [])
      .filter((message) => message?.role === "user" && message?.reply_to)
      .map((message) => String(message.reply_to)),
  );

export const percent = (rate) =>
  typeof rate === "number" && Number.isFinite(rate)
    ? `${Math.round(rate * 100)}%`
    : "\u2014";

// StatusChip infers its state from the string it is given, and "completed" matches
// neither "pass" nor "ok". Map the stages explicitly: the terminal outcomes get
// pass/error/canceled, and everything still in flight reads running.
export const stageStatus = (stage) => {
  if (stage === "completed") return STATUS_TYPES.PASS;
  if (stage === "failed") return STATUS_TYPES.ERROR;
  if (stage === "canceled") return STATUS_TYPES.CANCELED;
  return STATUS_TYPES.RUNNING;
};

// credentials.detected_connectors is how ALK reports what the submitted source talks to.
// It is lexical detection over the source files, so it is multi-valued and a voice agent
// that also serves HTTP reports both — check for a voice transport before falling back to chat.
const VOICE_CONNECTORS = ["livekit", "vapi", "retell", "twilio", "pipecat"];

export const agentTypeIcon = (item) => {
  const connectors = item?.credentials?.detected_connectors || [];
  if (connectors.some((name) => VOICE_CONNECTORS.includes(name))) {
    return { src: "/assets/icons/ic_voice.svg", label: "Voice agent" };
  }
  if (connectors.includes("http")) {
    return { src: "/assets/icons/ic_chat_single.svg", label: "Chat agent" };
  }
  // Nothing detected, or only non-transport signals such as mcp. Those say what the
  // agent uses, not how a caller reaches it, so the type genuinely is not known yet.
  return { src: "/assets/icons/ic_bot.svg", label: "Not detected" };
};

export const errorMessage = (error) => {
  if (typeof error === "string" && error.trim()) return error;
  // Axios keeps the actionable API payload under response.data. Contract
  // validation errors instead expose their explanation directly on the Error.
  const payload = error?.response?.data || error;
  const detail = payload?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  // DRF field errors arrive as {field: ["message"]} rather than a string.
  if (detail && typeof detail === "object") {
    const first = Object.values(detail).flat().find(Boolean);
    if (typeof first === "string") return first;
  }
  // A harness error body carries a code in `error` and the sentence in `message`.
  if (typeof payload?.message === "string" && payload.message.trim())
    return payload.message;
  if (typeof payload?.error === "string" && payload.error.trim())
    return payload.error;
  if (typeof error?.message === "string" && error.message.trim())
    return error.message;
  return "Something went wrong";
};
