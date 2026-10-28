import { agentSourceOf } from "../_mock/agentRegistry";
import { versionNameOf } from "../_mock/codeRefs";
import { agentChannelsOf } from "../_mock/agentLifecycle";

/*
  One AgentPicker row for an agent: its type from the environment it runs in,
  where it lives (without its connection state — "GitHub · app not installed"
  is GitHub), its production version and how many environments test it.
  With no environment yet, its type comes from the channels it serves.
  A git agent's version reads as its commit ("main @ 3f9a2c1"), never vN.
  `versionLabel` names another version than production — the one a run tested.
*/
export const agentTypeOf = (env) => (env?.surface === "voice" ? "Voice" : "Chat");

export function agentPickerChoice(agent, env, status = null, versionLabel = null) {
  const label = versionLabel || agent.production || null;
  return {
    key: agent.key,
    name: agent.name,
    type: env || (agent.usedIn || []).length
      ? agentTypeOf(env)
      : agentTypeOf({ surface: agentChannelsOf(agent)[0] }),
    source: String(agentSourceOf(agent).label || "").split(" · ")[0],
    version: label ? versionNameOf(agent, label).main : "—",
    versionTested: !!versionLabel,
    envs: (agent.usedIn || []).length,
    off: !!status && status.status !== "connected",
    offReason: status?.label,
  };
}

/* The line under the field: "Voice · in production v12 · runs in Support line · 2 more environments". */
export function agentPickerHelper(choice, envName) {
  const more = choice.envs - 1;
  return `${choice.type} · ${choice.versionTested ? "this run tested" : "in production"} ${choice.version} · runs in ${envName || "its main environment"}${more > 0 ? ` · ${more} more ${more === 1 ? "environment" : "environments"}` : ""}`;
}
