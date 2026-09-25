import { AGENT_TYPES } from "src/sections/agents/constants";
import {
  environmentName,
  HARNESS_STAGE,
  terminalStages,
} from "src/pages/dashboard/harness/harnessShared";
import { ENV_STATUS, BUILD_STATUS } from "../myEnvironments.constants";

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
export const envStatusFor = (stage, status) => {
  if (STAGE_LED_STAGES.has(stage)) return stageToStatus(stage);
  return status === HARNESS_STAGE.COMPLETED ? ENV_STATUS.READY : status;
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

// Map one harness-environments list row to the flat row the My Environments
// table reads. Unlike the harness-jobs projection, this payload carries the
// real description and the tool / scenario counts, and its `status` is already
// the four-state pill value. Sub-goals and total runs have no field in the list
// yet, so they stay null and their columns keep a placeholder header.
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
    // §1 (updated contract) adds sub_goals_count / runs_count. They are read
    // defensively: null until the backend actually serves them, at which point
    // the real values flow through and the "coming soon" column headers can be
    // dropped (Phase B). buildProgress feeds the status pill's inline "n/m
    // steps" and the list carries no live progress, so it stays absent.
    subgoals: item?.sub_goals_count ?? null,
    runsTotal: item?.runs_count ?? null,
    buildProgress: null,
  };
}
