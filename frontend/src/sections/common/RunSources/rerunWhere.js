// options-truth L3 G9 (fix 4): which connected machines can take a rerun.
// A machine serves a run only when one of its connected agents is that
// run's agent: the run's own agent id, else the same catalog entry (the
// framework, harness or voice provider). The dialog lists the others
// disabled with the reason, and the mock answers 409 for them. Pure.
import { entryOfConfig } from "./catalog";

/** The catalog entry id a run's agent connects as (`crewai`, `claude_code`). */
export function runEntryIdOf(config = {}) {
  try {
    return entryOfConfig(config)?.id ?? null;
  } catch {
    return null;
  }
}

/** The connected agent on `machine` that serves the run, or null. */
export function servingAgentOf(machine, config = {}) {
  const agents = machine?.agents ?? [];
  if (!agents.length) return null;
  const ownId = config?.execution?.agent_id ?? null;
  if (ownId) {
    const own = agents.find((a) => a.id === ownId);
    if (own) return own;
  }
  const entryId = runEntryIdOf(config);
  if (!entryId) return null;
  return agents.find((a) => a.entry_id === entryId) ?? null;
}

/** `CrewAI support crew` — the run's agent in words (pure). */
export function runAgentNameOf(run = {}) {
  const c = run?.config ?? {};
  const name =
    c.execution?.agent_name ?? String(run?.name ?? "").split(" · ")[0];
  return name || "this agent";
}

/**
 * Why `machine` cannot take the run (its agents serve something else), or
 * null when one of them serves it:
 * `voice-worker@acme-gpu-01 serves acme-voice-v2, not CrewAI support crew`.
 */
export function serveBlockOf(machine, run = {}) {
  if (!machine) return null;
  if (servingAgentOf(machine, run?.config ?? {})) return null;
  const names = (machine.agents ?? []).map((a) => a.name).filter(Boolean);
  const what = names.length ? names.join(", ") : "no connected agent";
  return `${machine.name} serves ${what}, not ${runAgentNameOf(run)}`;
}

/**
 * options-truth L5 G2: the model a prompt or agent run really called: the
 * prompt's model, else the agent's own model, else the model of its last
 * llm node (the one that writes the reply), as the run engine reads it.
 */
export function runModelOf(config = {}) {
  if (config?.model?.id) return config.model.id;
  if (config?.agent?.model) return config.agent.model;
  const llms = (config?.agent?.nodes ?? []).filter(
    (n) => n?.type === "llm" && n.model,
  );
  return llms.length ? llms[llms.length - 1].model : null;
}
