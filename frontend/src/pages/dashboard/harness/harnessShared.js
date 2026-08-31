import { format, isToday } from "date-fns";

import { STATUS_TYPES } from "src/utils/statusUtils";

export const terminalStages = new Set(["completed", "failed", "canceled"]);

// The ordered pipeline, mirroring HarnessStage in the ALK wheel (fi/alk/harness/job.py).
// "failed" and "canceled" are outcomes rather than positions, so they stay out: a stage
// missing from this list indexes to -1, which strands the checklist showing nothing reached
// and pins the progress bar at its 2% floor.
export const stages = [
  "queued",
  "acquiring_source",
  "understanding_agent",
  "generating_environment",
  "building_environment",
  "validating_environment",
  "generating_data",
  "generating_scenarios",
  "validating_scenarios",
  "connecting_agent",
  "running",
  "grading",
  "uploading_artifacts",
  "cleaning_up",
  "completed",
];

// Event timestamps are full ISO instants, but a time-only label is ambiguous the moment a run
// crosses midnight or is reopened on a later day. Show the date whenever it is not today.
export const eventTime = (value) => {
  if (!value) return "";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  return isToday(at) ? format(at, "pp") : format(at, "d MMM, pp");
};

// ALK reports events against a coarser vocabulary than the checklist shows: one "environment"
// event covers four UI stages. Six stages (building/validating environment, generating data,
// validating scenarios, connecting agent, grading) are never emitted at all, so they can only
// be credited through the group they belong to.
const EVENT_STAGE_GROUPS = {
  understand: ["understanding_agent"],
  environment: [
    "generating_environment",
    "building_environment",
    "validating_environment",
    "generating_data",
  ],
  scenarios: ["generating_scenarios", "validating_scenarios"],
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

const eventMillis = (event) => {
  const at = new Date(event?.wall_time ?? "").getTime();
  return Number.isNaN(at) ? null : at;
};

// Wall-clock time the run has been going. There is no started_at on the job, so the first
// event is the earliest moment we can prove; a run with no events yet is unmeasurable.
export const runElapsed = (
  events = [],
  now = Date.now(),
  isTerminal = false,
) => {
  const stamps = events.map(eventMillis).filter((at) => at !== null);
  if (!stamps.length) return null;
  const first = Math.min(...stamps);
  const end = isTerminal ? Math.max(...stamps) : now;
  return Math.max(0, end - first);
};

// Time spent in the stage the run is in — but only six of the fifteen stages emit events, so
// this is null for the rest. Callers must omit the line rather than render a zero, which would
// read as "instant" when it means "not reported".
export const stageElapsed = (status, events = [], now = Date.now()) => {
  const group = Object.entries(EVENT_STAGE_GROUPS).find(
    ([, members]) => members[0] === status?.stage,
  );
  if (!group) return null;
  const started = events
    .filter(
      (event) =>
        event?.payload?.stage === group[0] && event?.type?.endsWith(".started"),
    )
    .map(eventMillis)
    .filter((at) => at !== null);
  if (!started.length) return null;
  return Math.max(0, now - Math.max(...started));
};

// Compact and stable: a stage timer that reflows the column every time it crosses a digit is
// worse than one that does not.
export const shortDuration = (ms) => {
  if (ms === null || ms === undefined) return null;
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }
  return minutes
    ? `${minutes}m ${String(seconds).padStart(2, "0")}s`
    : `${seconds}s`;
};

// Run ids are "harness-<uuid>" — 44 characters that nobody reads and everybody copies. Show
// the prefix plus the uuid's first group, which is enough to tell two runs apart on screen,
// and leave the full value to the copy button beside it.
export const shortRunId = (runId = "") => {
  const parts = String(runId).split("-");
  return parts.length > 2 ? `${parts[0]}-${parts[1]}` : String(runId);
};

// Authoring writes `metadata.name`, older jobs `metadata.agent_name`. Never fall back to the
// job id: it reads as a name and search cannot match it. Callers whose slot cannot be blank
// pass their own fallback.
export const environmentName = (job, fallback = "\u2014") =>
  job?.metadata?.agent_name || job?.metadata?.name || fallback;

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

export const TAB_STATE = {
  DONE: "done",
  PENDING: "pending",
  WORKING: "working",
};

// Which pipeline stages produce each tab's content. The checklist is finer-grained than the
// tabs: four stages feed Environment, and everything after the scenarios are validated feeds
// Runs, which is also the catch-all.
const TAB_STAGES = {
  contract: ["understanding_agent"],
  environment: [
    "generating_environment",
    "building_environment",
    "validating_environment",
    "generating_data",
  ],
  scenarios: ["generating_scenarios", "validating_scenarios"],
  runs: [
    "connecting_agent",
    "running",
    "grading",
    "uploading_artifacts",
    "cleaning_up",
  ],
};

// What a tab should say about itself: working while the runner is inside any of its stages,
// done once it has an artifact or the run has moved past all of them. `hasOutput` wins on its
// own because a tab can hold an artifact from a stage the checklist never emits an event for.
export const tabState = (tab, status, events = [], hasOutput = false) => {
  const states = (TAB_STAGES[tab] || []).map((name) =>
    stageState(status, stages.indexOf(name), events),
  );
  if (states.includes(STAGE_STATE.ACTIVE)) return TAB_STATE.WORKING;
  if (
    hasOutput ||
    (states.length && states.every((s) => s === STAGE_STATE.DONE))
  )
    return TAB_STATE.DONE;
  return TAB_STATE.PENDING;
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

// Icon column: the sidebar's 18px glyph plus its gutter.
export const ICON_SIZE = 18;
export const ICON_GUTTER = 10;

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

export function eventMessage(event) {
  const payload = event.payload || {};
  if (payload.detail) return String(payload.detail);
  if (payload.message) return String(payload.message);
  if (payload.stage) {
    // "Calls completed" reads better than "Calls Harness.stage.completed".
    if (event.type?.endsWith(".started"))
      return `${readable(payload.stage)} started`;
    if (event.type?.endsWith(".completed"))
      return `${readable(payload.stage)} completed`;
    if (event.type?.endsWith(".failed"))
      return `${readable(payload.stage)} failed`;
    return `${readable(payload.stage)} updated`;
  }
  return readable(event.type || "Progress updated");
}

// A completed run is 100% regardless of where its stage landed in the list; anything else
// sits half a stage into its slot, with a floor so a queued run still shows a sliver.
export const jobProgress = (status) => {
  if (!status) return 0;
  if (status.stage === "completed") return 100;
  const index = stages.indexOf(status.stage);
  return Math.max(2, ((Math.max(index, 0) + 0.5) / stages.length) * 100);
};

// The axios interceptor rejects with a FLAT object — {...response.data, statusCode} — so
// there is no `.response` to read through. Harness views return a bare {"detail": "..."},
// while the platform envelope carries both detail and message, so detail comes first.
// What became of a mid-run change. ALK never finalises an adjustment it did not reach, so
// one left "pending" on a run that has stopped is not in flight — it was stranded, and
// saying "will land at X" about a dead run is a promise the platform cannot keep.
export const adjustmentStatus = (adjustment, jobStage) => {
  if (adjustment.status === "applied")
    return `Applied at ${readable(adjustment.applied_stage || adjustment.target_stage)}`;
  if (jobStage === "canceled")
    return "Not applied — the run was canceled first";
  if (jobStage === "failed") return "Not applied — the run failed first";
  if (jobStage === "completed") return "Not applied — the run finished first";
  return `${readable(adjustment.status)} · will land at ${readable(adjustment.target_stage)}`;
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
  if (typeof payload?.error === "string" && payload.error.trim())
    return payload.error;
  if (typeof payload?.message === "string" && payload.message.trim())
    return payload.message;
  if (typeof error?.message === "string" && error.message.trim())
    return error.message;
  return "Something went wrong";
};
