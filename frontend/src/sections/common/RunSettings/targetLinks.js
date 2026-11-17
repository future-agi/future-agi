// W6-1 (A2, `prompt:open-in-prompts`): the links that open a run's own
// target, so every "Open in Prompts" lands on the run's prompt version and
// every "Open in Agent Playground" on its graph version (not the latest or
// the draft). Pure; the leaderboard ⋯, run details, the results drawer and
// its output card all build their links here.
//   Prompts reads `?version=<version name>` (WorkbenchProvider, V3);
//   Agent Playground reads `?version=<version id>` (AgentPlaygroundView).

/** `/dashboard/workbench/create/<prompt>?tab=Playground&version=V3`, or null. */
export function promptLinkOf(target, { config } = {}) {
  const id = target?.prompt_id ?? config?.prompt?.id ?? null;
  if (!id) return null;
  const version =
    target?.prompt_version_name ??
    target?.prompt_version ??
    config?.prompt?.version ??
    null;
  const v = version ? `&version=${encodeURIComponent(version)}` : "";
  return `/dashboard/workbench/create/${id}?tab=Playground${v}`;
}

/**
 * `/dashboard/agents/playground/<graph>/build?version=<version id>`, or null.
 * The version is the one the run ran: its config's (the engine reads
 * `config.agent.version_id`; a Test a fix child records the version it
 * saved there while it copies its parent's target, M8 publishGate), else
 * its target's.
 */
export function agentLinkOf(target, { config } = {}) {
  const graph =
    target?.graph_id ?? config?.agent?.id ?? config?.agent?.graph_id ?? null;
  if (!graph) return null;
  const vid = config?.agent?.version_id ?? target?.version_id ?? null;
  const v = vid ? `?version=${encodeURIComponent(vid)}` : "";
  return `/dashboard/agents/playground/${graph}/build${v}`;
}
