import {
  AGENT_TYPE_GROUPS,
  GROUP_BLURBS,
  PREBUILT_AGENT_TYPE_GROUP,
} from "../prebuiltEnvironments.constants";

// Bucket environments under the agent-type group they need, in the same order
// the connect screen lists those groups. Empty groups drop out.
export const groupByAgentGroup = (envs) =>
  AGENT_TYPE_GROUPS.map((group) => ({
    id: group,
    label: group,
    blurb: GROUP_BLURBS[group],
    items: (envs ?? []).filter((e) => PREBUILT_AGENT_TYPE_GROUP[e.agentType] === group),
  })).filter((g) => g.items.length);
