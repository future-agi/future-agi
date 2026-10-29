/*
  Copying an environment to another agent (roadmap builder.md Week 2): what
  the new agent changes before the copy is made — which of the world's tools
  it calls, which cases it can't be measured on, what stays the
  environment's own, and what still compares with the source.
*/
import { agentToolsFor, knownToolsFor, requiredToolsOf } from "./toolFit";
import { agentFilesOf } from "./agentFiles";
import { rulesOf } from "./envModel";

/** The tools an agent calls, read from where it's tested, else its definition. */
export const agentCallsOf = (agent, state) => {
  const home = (agent?.usedIn || [])[0];
  const homeEnv =
    home && state?.myEnvironments?.find((e) => e.id === home.envId);
  const homeState = home && state?.byEnv?.[home.envId];
  if (homeEnv && homeState)
    return agentToolsFor(homeEnv, homeState, agent.latest).map((t) => t.name);
  try {
    const version = (agent?.versions || [])[agent.versions.length - 1];
    return agentFilesOf({
      agent,
      version,
      env: null,
      envState: null,
    }).definition.tools.items.map((t) => t.name);
  } catch {
    return [];
  }
};

/**
 * The agent change in a fork, reviewed: tools, unmeasurable cases, what
 * stays, and what compares. `scenarioIds` is the copy's scope.
 */
export const forkBindingOf = ({
  env,
  envState,
  target,
  state,
  scenarioIds,
}) => {
  const calls = new Set(agentCallsOf(target, state));
  const world = (env?.tools || []).map((t) => t.name);
  const known = knownToolsFor(env, envState);
  const rows = (envState?.scenarios || []).filter(
    (s) =>
      !s.retired &&
      !s.archived &&
      !s.proposed &&
      (!scenarioIds?.length || scenarioIds.includes(s.id)),
  );
  const unmeasurable = rows
    .map((s) => ({
      s,
      missing: requiredToolsOf(s, known).filter(
        (t) => calls.size && !calls.has(t),
      ),
    }))
    .filter((x) => x.missing.length)
    .map(({ s, missing }) => ({
      id: s.id,
      title: s.title || s.name || s.id,
      missing,
    }));
  const unanswered = [...calls].filter((t) => !world.includes(t));
  return {
    known: calls.size > 0,
    covers: world.filter((t) => calls.has(t)),
    unused: world.filter((t) => !calls.has(t)),
    unanswered,
    cases: rows.length,
    unmeasurable,
    rules: rulesOf(env, envState).length,
    comparable: rows.length - unmeasurable.length,
  };
};
