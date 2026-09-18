import { AGENT_TYPES } from "src/sections/agents/constants";
import { environmentName } from "src/pages/dashboard/harness/harnessShared";
import { ENV_STATUS } from "../myEnvironments.constants";

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

// The harness pipeline reports fine-grained stages; the table only needs the
// four run-states its status pill knows. "failed" and "canceled" are both
// outcomes the pill draws in red, and every stage before the terminal ones is
// still assembling the environment.
export const stageToStatus = (stage) => {
  if (stage === "completed") return ENV_STATUS.COMPLETED;
  if (stage === "failed" || stage === "canceled") return ENV_STATUS.FAILED;
  if (stage === "running") return ENV_STATUS.RUNNING;
  return ENV_STATUS.BUILDING;
};

const agentTypeFor = (connectors = []) =>
  connectors.some((name) => VOICE_CONNECTORS.includes(name))
    ? AGENT_TYPES.VOICE
    : AGENT_TYPES.CHAT;

// Map one harness-jobs list item ({ job, status, credentials }) to the flat row
// the My Environments table reads. The list payload carries no description,
// tools, scenarios, sub-goals, run count or build progress, so those are left
// as placeholders until the real environments endpoint lands.
export function harnessJobToRow(item) {
  return {
    id: item?.job?.job_id,
    name: environmentName(item?.job),
    status: stageToStatus(item?.status?.stage),
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
    status: item?.status,
    agentType: ENV_AGENT_TYPE[item?.agent_type],
    updatedAt: item?.last_updated ?? item?.created_at ?? null,
    description: item?.description ?? null,
    tools: item?.tools_count ?? null,
    scenarios: item?.scenario_count ?? null,
    // Sub-goals and total runs have no field in the list yet; the table keeps a
    // "coming soon" header over them and their cells render a dash. buildProgress
    // feeds the status pill's inline "n/m steps" and the list carries no live
    // progress, so it is absent too.
    subgoals: null,
    runsTotal: null,
    buildProgress: null,
  };
}
