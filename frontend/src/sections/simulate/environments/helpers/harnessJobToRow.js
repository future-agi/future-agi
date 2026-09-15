import { AGENT_TYPES } from "src/sections/agents/constants";
import { environmentName } from "src/pages/dashboard/harness/harnessShared";
import { ENV_STATUS } from "../myEnvironments.constants";

// ALK reports the transports it detected in the source under
// credentials.detected_connectors (see `agentTypeIcon` in harnessShared). A
// voice agent that also serves HTTP lists both, so a voice transport wins.
const VOICE_CONNECTORS = ["livekit", "vapi", "retell", "twilio", "pipecat"];

// The harness pipeline reports fine-grained stages; the table only needs the
// four run-states its status pill knows. "failed" and "canceled" are both
// outcomes the pill draws in red, and every stage before the terminal ones is
// still assembling the environment.
const stageToStatus = (stage) => {
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
// as placeholders until the real environments endpoint lands (TH-7962).
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
