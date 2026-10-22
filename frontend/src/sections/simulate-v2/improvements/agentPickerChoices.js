import { agentSourceOf } from "../_mock/agentRegistry";

/*
  One AgentPicker row for an agent: its type from the environment it runs in,
  where it lives (without its connection state — "GitHub · app not installed"
  is GitHub), its production version and how many environments test it.
*/
export const agentTypeOf = (env) => (env?.surface === "voice" ? "Voice" : "Chat");

export function agentPickerChoice(agent, env, status = null) {
  return {
    key: agent.key,
    name: agent.name,
    type: agentTypeOf(env),
    source: String(agentSourceOf(agent).label || "").split(" · ")[0],
    version: agent.production || "—",
    envs: (agent.usedIn || []).length,
    off: !!status && status.status !== "connected",
    offReason: status?.label,
  };
}

/* The line under the field: "Voice · in production v12 · runs in Support line · 2 more environments". */
export function agentPickerHelper(choice, envName) {
  const more = choice.envs - 1;
  return `${choice.type} · in production ${choice.version} · runs in ${envName || "its main environment"}${more > 0 ? ` · ${more} more ${more === 1 ? "environment" : "environments"}` : ""}`;
}
