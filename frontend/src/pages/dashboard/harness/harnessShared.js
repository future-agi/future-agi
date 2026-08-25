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
// event covers four UI stages. Map each event stage to the UI stage its group STARTS at, so a
// group that merely started is never credited with finishing its later stages.
const EVENT_STAGE_GROUP_START = {
  understand: "understanding_agent",
  environment: "generating_environment",
  scenarios: "generating_scenarios",
  calls: "connecting_agent",
  uploading_artifacts: "uploading_artifacts",
  cleaning_up: "cleaning_up",
  completed: "completed",
};

const groupStartIndexes = () =>
  Object.values(EVENT_STAGE_GROUP_START)
    .map((stage) => stages.indexOf(stage))
    .filter((index) => index >= 0)
    .sort((a, b) => a - b);

// A cancel names no stage, unlike a failure. Reconstruct how far the run got from the events
// instead: everything before the reached group is done, and the group's first stage is where it
// stopped. Returns { doneThrough, stoppedAt }, both -1 when there is nothing to anchor on.
export const canceledProgress = (events = []) => {
  let reached = -1;
  let finished = false;
  events.forEach((event) => {
    const stage = event?.payload?.stage;
    const mapped = EVENT_STAGE_GROUP_START[stage];
    if (!mapped) return;
    const index = stages.indexOf(mapped);
    if (index < 0 || index < reached) return;
    const completed = event?.type?.endsWith(".completed");
    if (index > reached) {
      reached = index;
      finished = completed;
    } else if (completed) {
      finished = true;
    }
  });

  if (reached < 0) return { doneThrough: -1, stoppedAt: -1 };
  if (!finished) return { doneThrough: reached - 1, stoppedAt: reached };

  // The group finished, so every stage up to the next group's start is done.
  const next = groupStartIndexes().find((index) => index > reached);
  const end = (next === undefined ? stages.length : next) - 1;
  return {
    doneThrough: end,
    stoppedAt: end + 1 < stages.length ? end + 1 : -1,
  };
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
    const { doneThrough, stoppedAt } = canceledProgress(events);
    if (doneThrough < 0 && stoppedAt < 0) return STAGE_STATE.PENDING;
    if (index <= doneThrough) return STAGE_STATE.DONE;
    if (index === stoppedAt) return STAGE_STATE.STOPPED;
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

export const readable = (value = "") =>
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
  if (payload.stage)
    return `${readable(payload.stage)} ${readable(event.type)}`;
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

export const errorMessage = (error) =>
  error?.response?.data?.detail || error?.message || "Something went wrong";
