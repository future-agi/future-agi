import { paths } from "src/routes/paths";

/*
  Every run starts from the run plan — scope, estimate and limit confirmed
  first. A Run or Re-run anywhere else in the product links here: the
  environment opens with its run plan prefilled.
    only       scenario ids (empty: every runnable scenario)
    trials     attempts per scenario
    agent      the agent version to run
    also       other environments to run alongside, as a run group
    continues  the run whose unstarted scenarios this one finishes
    snapshot   play scenario text as it was in that run
    envVersion the environment version that snapshot ran on
*/
export const runPlanHref = (
  envId,
  { only = [], trials, agent, also = [], continues, snapshot, envVersion, toolReplay } = {},
) => {
  const q = new URLSearchParams({ plan: "1" });
  if (only?.length) q.set("only", only.join(","));
  if (trials) q.set("trials", String(trials));
  if (agent) q.set("agent", agent);
  if (also?.length) q.set("also", also.join(","));
  if (continues) q.set("continues", continues);
  if (snapshot) q.set("snapshot", snapshot);
  if (envVersion) q.set("env", envVersion);
  /* Recorded-tool replay: tools answer with what they returned in that run. */
  if (toolReplay) q.set("replay", toolReplay);
  return `${paths.dashboard.simulate.environmentStep(envId, "overview")}?${q}`;
};

/** The run-plan request a planned link carries, or null. */
export const runPlanRequestOf = (searchParams) => {
  if (searchParams.get("plan") !== "1") return null;
  const list = (k) => (searchParams.get(k) || "").split(",").filter(Boolean);
  return {
    scenarioIds: list("only"),
    trials: Number(searchParams.get("trials")) || undefined,
    agent: searchParams.get("agent") || undefined,
    also: list("also"),
    continues: searchParams.get("continues") || undefined,
    snapshot: searchParams.get("snapshot") || undefined,
    envVersion: searchParams.get("env") || undefined,
    toolReplay: searchParams.get("replay") || undefined,
  };
};

export const RUN_PLAN_PARAMS = [
  "plan",
  "only",
  "trials",
  "agent",
  "also",
  "continues",
  "snapshot",
  "env",
  "replay",
];
