/*
  "Running now" (clarified plan, 30 Oct): while anything runs, a count sits
  next to Environments in the side menu — an indicator only, no list or Jobs
  page. The side menu sits outside the simulation store, so the store
  publishes the count here and the menu reads it.
*/
import { useSyncExternalStore } from "react";
import { envAgentKey } from "./agentRegistry";
import { observeProjectById } from "./connectAgent";

const KEY = "fagi.simulate.runningNow";
const EVENT = "fagi:running-now";

/** Work started and not finished, across every environment: runs, RSI
    fixes (improvements) and builder jobs (tasks.md T33). */
export const runningCountOf = (state) =>
  Object.values(state?.byEnv || {}).reduce(
    (n, es) =>
      n +
      (es?.runs || []).filter(
        (r) =>
          !r.finishedAt &&
          !r.pending &&
          String(r.status || "").toLowerCase() === "running",
      ).length +
      (es?.optimizations || []).filter((o) => o.status === "running").length,
    0,
  ) +
  (state?.myEnvironments || []).filter((e) => e.buildStatus === "building")
    .length;

export const publishRunning = (n) => {
  try {
    if (window.localStorage.getItem(KEY) === String(n)) return;
    window.localStorage.setItem(KEY, String(n));
  } catch {
    /* storage blocked: the menu just shows no count */
  }
  window.dispatchEvent(new Event(EVENT));
};

const read = () => {
  try {
    return Number(window.localStorage.getItem(KEY)) || 0;
  } catch {
    return 0;
  }
};
const subscribe = (cb) => {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
};

/** The count for the side menu. */
export const useRunningNow = () =>
  useSyncExternalStore(subscribe, read, () => 0);

/*
  Observe → environment: which environment each Observe project's agent is
  tested in, so Observe's "Create environment test" can open that
  environment's import review. Published by the store like the running count.
*/
const PROJ_KEY = "fagi.simulate.observeEnvs";
export const observeEnvsOf = (state) => {
  const out = [];
  const seen = new Set();
  const add = (projectId, projectName, e) => {
    const k = `${projectId}::${e.id}`;
    if (!projectId || seen.has(k)) return;
    seen.add(k);
    out.push({
      projectId: String(projectId),
      projectName: projectName || null,
      envId: e.id,
      envName: e.name,
    });
  };
  (state?.myEnvironments || []).forEach((e) => {
    const agent = state?.byEnv?.[e.id]?.agent;
    const project = agent?.values?.observeProject;
    if (project?.id) add(project.id, project.name, e);
    /* The link Connect traces saves (project → agent): every environment
       that tests that agent takes the project's traces. */
    const key = envAgentKey(agent, e.id);
    Object.entries(state?.projectLinks || {}).forEach(([p, k]) => {
      if (key && k === key) add(p, observeProjectById(p)?.name, e);
    });
  });
  return out;
};
export const publishObserveEnvs = (list) => {
  try {
    const next = JSON.stringify(list);
    if (window.localStorage.getItem(PROJ_KEY) !== next)
      window.localStorage.setItem(PROJ_KEY, next);
  } catch {
    /* storage blocked: Observe falls back to the Environments list */
  }
};
/** The environments an Observe project's traces can become tests in. */
export const envsForObserveProject = (projectId) => {
  try {
    return (
      JSON.parse(window.localStorage.getItem(PROJ_KEY) || "[]") || []
    ).filter((x) => String(x.projectId) === String(projectId));
  } catch {
    return [];
  }
};
