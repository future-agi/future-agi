import { AGENT_TYPES } from "src/sections/agents/constants";
import {
  environmentName,
  HARNESS_STAGE,
  terminalStages,
} from "src/pages/dashboard/harness/harnessShared";
import { ENV_STATUS, BUILD_STATUS, NOT_PROVIDED } from "../myEnvironments.constants";

// ALK reports the transports it detected in the source under
// credentials.detected_connectors (see `agentTypeIcon` in harnessShared). A
// voice agent that also serves HTTP lists both, so a voice transport wins.
export const VOICE_CONNECTORS = [
  "livekit",
  "vapi",
  "retell",
  "twilio",
  "pipecat",
];

const FINALIZING_STAGES = new Set([
  HARNESS_STAGE.FINALIZING,
  HARNESS_STAGE.CLEANING_UP,
]);

const STAGE_LED_STAGES = new Set([
  ...FINALIZING_STAGES,
  HARNESS_STAGE.CANCELED,
]);

// The harness pipeline reports fine-grained stages; the table only needs the
// states its status pill knows. "failed" is a real failure, "canceled" is the
// user's own cancel, cleanup reads as finalizing, and every stage before the
// terminal ones is still assembling the environment. A finished build is Ready,
// never Completed: more simulations can always run in it (PRD §7.1).
export const stageToStatus = (stage) => {
  if (stage === HARNESS_STAGE.COMPLETED) return ENV_STATUS.READY;
  if (stage === HARNESS_STAGE.FAILED) return ENV_STATUS.FAILED;
  if (stage === HARNESS_STAGE.CANCELED) return ENV_STATUS.CANCELLED;
  if (FINALIZING_STAGES.has(stage)) return ENV_STATUS.FINALIZING;
  if (stage === HARNESS_STAGE.RUNNING) return ENV_STATUS.RUNNING;
  return ENV_STATUS.BUILDING;
};

// The list endpoint still reports a built environment as "completed"; it reads
// as Ready here for the same reason stageToStatus says so.
const LIST_STATUS_COMPLETED = "completed";

export const envStatusFor = (stage, status) => {
  if (STAGE_LED_STAGES.has(stage)) return stageToStatus(stage);
  return status === LIST_STATUS_COMPLETED ? ENV_STATUS.READY : status;
};

export const jobStatusFor = (status) =>
  status?.cancel_requested_at && !terminalStages.has(status?.stage)
    ? ENV_STATUS.CANCELLING
    : stageToStatus(status?.stage);

// The environment's build lifecycle from a job stage. Three-way, unlike the old
// inline `stage === "completed" ? "ready" : "building"` which mislabelled a
// terminal-failed job as still building (the header stuck on "Building" with the
// animation never stopping). A failed/canceled stage is now BUILD_STATUS.FAILED.
export const buildStatusFor = (stage) => {
  if (stage === HARNESS_STAGE.COMPLETED || stage === HARNESS_STAGE.RUNNING) return BUILD_STATUS.READY;
  if (stage === HARNESS_STAGE.FAILED || stage === HARNESS_STAGE.CANCELED) return BUILD_STATUS.FAILED;
  return BUILD_STATUS.BUILDING;
};

// A voice transport in the detected connectors wins; any other detected
// connector is a chat agent. With NOTHING detected we can't tell — return null
// so the table shows "Not identified" rather than misreporting the env as Chat.
const agentTypeFor = (connectors = []) => {
  if (!connectors?.length) return null;
  return connectors.some((name) => VOICE_CONNECTORS.includes(name))
    ? AGENT_TYPES.VOICE
    : AGENT_TYPES.CHAT;
};

// Map one harness-jobs list item ({ job, status, credentials }) to the flat row
// the My Environments table reads. The list payload carries no description,
// tools, scenarios, sub-goals, run count or build progress, so those are left
// as placeholders until the real environments endpoint lands.
export function harnessJobToRow(item) {
  return {
    id: item?.job?.job_id,
    name: environmentName(item?.job),
    status: jobStatusFor(item?.status),
    agentType: agentTypeFor(item?.credentials?.detected_connectors),
    updatedAt: item?.status?.updated_at ?? null,
    description: null,
    tools: null,
    scenarios: null,
    subgoals: null,
    runsTotal: 0,
    buildProgress: null,
  };
}

// The environments list reports agent type as the two live modalities; the
// table's icon column speaks AGENT_TYPES.
const ENV_AGENT_TYPE = {
  voice: AGENT_TYPES.VOICE,
  chat: AGENT_TYPES.CHAT,
};

// The value the API sent for `key`, null included, or NOT_PROVIDED when the
// key isn't in the payload at all.
const sent = (obj, key) => (obj && Object.hasOwn(obj, key) ? obj[key] : NOT_PROVIDED);

// The last run's pass rate on the scenarios it scored (skipped ones don't
// count), and how it moved against the run before it. Null when nothing was
// scored, so the cell reads "-" rather than 0%.
const lastRunOf = (run) => {
  const passed = run?.outcome_passed ?? 0;
  const failed = run?.outcome_failed ?? 0;
  if (!run || passed + failed === 0) return null;
  const rate = Math.round((passed / (passed + failed)) * 100);
  const previous = sent(run, "previous_pass_rate");
  const delta =
    previous === NOT_PROVIDED || previous === null
      ? previous
      : Math.round(rate - previous);
  return { rate, delta };
};

// Map one harness-environments list row to the flat row the My Environments
// table reads. Unlike the harness-jobs projection, this payload carries the
// real description and the tool / scenario counts, and its `status` is already
// the four-state pill value.
export function harnessEnvToRow(item) {
  return {
    id: item?.id,
    name: item?.name,
    // §1 always reports status and agent_type, so these are passed through
    // rather than defaulted — inventing "building"/"chat" for a missing value
    // would hide a real data gap. `agent_type` is one of two modalities.
    status: envStatusFor(item?.stage, item?.status),
    agentType: ENV_AGENT_TYPE[item?.agent_type],
    updatedAt: item?.last_updated ?? item?.created_at ?? null,
    description: item?.description ?? null,
    domain: item?.domain ?? null,
    tools: item?.tools_count ?? null,
    scenarios: item?.scenario_count ?? null,
    // buildProgress feeds the status pill's inline "n/m steps"; the list
    // carries no live progress, so it stays absent.
    subgoals: item?.sub_goals_count ?? null,
    runsTotal: item?.runs_count ?? null,
    buildProgress: null,
    agent: sent(item, "agent"),
    owner: sent(item, "owner"),
    // No environment versions, nor an agent's Main / Live / Sealed tags, in
    // the backend yet.
    envVersion: NOT_PROVIDED,
    agentTags: NOT_PROVIDED,
    // An older backend doesn't send last_run at all; null means never run.
    lastRun:
      sent(item, "last_run") === NOT_PROVIDED
        ? NOT_PROVIDED
        : lastRunOf(item.last_run),
  };
}
