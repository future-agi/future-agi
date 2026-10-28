/*
  A fix with its defaults — what the Fix button's price and its confirm
  start: the issue's scenarios, the agent's main environment, the agent's
  constraints and its whole run budget, the drafted diagnosis. The Fix page
  (Adjust) is the same plan with every one of these editable.
*/
import { useMemo } from "react";
import { agentDefinitionOf } from "../_mock/agentRegistry";
import { DEFAULT_LIMITS } from "../_mock/optimizationRuns";
import { budgetsWithin } from "../_mock/improvementStart";
import { textSearchDefault } from "../_mock/improvementPolicy";
import { limitsOf } from "../agents/agentLimits";
import { toolFit } from "../_mock/toolFit";
import { codeDiagnosisOf } from "./codeDiagnosis";
import {
  currentUser,
  defaultBatchId,
  stampProvenance,
} from "../_mock/scenarioProvenance";
import { suiteLogPatch } from "../_mock/suiteLog";
import {
  envsForAgent,
  fixEnvFor,
  fixEstimate,
  fixPreview,
  fixScenariosOf,
  issueScenarioDrafts,
  runnableDraftsOf,
  useIssueFix,
} from "./issueFix";

/* The longest a fix may take — the agent's own time cap, as the Fix page picks it. */
const MINUTES = [15, 30, 45, 60, 90, 120];
const minutesFor = (ceiling) =>
  MINUTES.filter((m) => m <= ceiling).at(-1) || ceiling;

export default function useFixPlan(issue, rootCause = null, envId = null) {
  const fix = useIssueFix(issue);
  const { state, agent, gate, start } = fix;
  const envs = useMemo(() => envsForAgent(agent, state), [agent, state]);
  /* Where it runs: the one picked for this fix, else the best fit — never
     a change to the agent's main environment. */
  const main =
    envs.find((e) => e.id === envId) || fixEnvFor(envs, issue) || null;
  const mainState = main ? state.byEnv[main.id] : null;
  /* The most common situations, up to the cap — the rest are picked in Adjust. */
  const scenarioSet = useMemo(
    () =>
      fixScenariosOf(issue ? issueScenarioDrafts(issue, main, mainState) : []),
    [issue, main, mainState],
  );
  /* Only the ones that can run here — what the first run will play. */
  const runnableSet = useMemo(
    () => runnableDraftsOf(main, mainState, scenarioSet.used),
    [main, mainState, scenarioSet.used],
  );
  const drafts = runnableSet.runnable;
  /* The issue's scenarios someone already added to the environment run once, as its own. */
  const inEnv = drafts.filter((d) =>
    (mainState?.scenarios || []).some((x) => x.id === d.id),
  ).length;
  const limits = useMemo(() => limitsOf(state, agent?.key), [state, agent]);
  const inText = main?.surface === "voice" && textSearchDefault(main?.surface);
  const preview = useMemo(
    () =>
      main
        ? fixPreview(main, mainState, issue, drafts, limits, {
            textSearch: inText,
            state,
            regressionEnvIds: [main.id],
          })
        : null,
    [main, mainState, issue, drafts, limits, inText, state],
  );
  const estimate = useMemo(
    () =>
      preview
        ? fixEstimate({
            preview,
            scenarioCount: drafts.length,
            regressionCount: 0,
            surface: main?.surface,
            inEnvCount: inEnv,
          })
        : null,
    [preview, drafts.length, main?.surface, inEnv],
  );
  const budgets = budgetsWithin(limits.budget.dollars);
  const diagnosis = useMemo(() => {
    if (!agent || !main) return null;
    const def = agentDefinitionOf({ agent, env: main, envState: mainState });
    return codeDiagnosisOf({
      issue,
      env: main,
      promptText: def?.instructions?.visible ? def.instructions.text : null,
      rootCause,
    });
  }, [agent, main, mainState, issue, rootCause]);

  /* Tools the agent calls that the environment can't answer — those calls
     come back not measured, so the fix has less to go on. */
  const toolGap = useMemo(() => {
    if (!main || !mainState) return [];
    try {
      return toolFit(main, mainState).missing.map((t) => t.name);
    } catch (e) {
      return [];
    }
  }, [main, mainState]);
  const consentNeeded = gate?.key === "consent";
  const building = main?.buildStatus === "building";
  const ready = !!agent && !!main && !building && drafts.length > 0;

  /* Start it with these defaults and a budget — the Fix page's own start. */
  const startFix = (budget) =>
    start({
      env: main,
      drafts,
      regressionEnvIds: [main.id],
      budget,
      minutes: minutesFor(
        limits.budget?.minutes || DEFAULT_LIMITS.budget.minutes,
      ),
      textSearch: inText,
      rootCause,
      diagnosis: diagnosis
        ? {
            where: diagnosis.where,
            cause: (diagnosis.cause || "").trim(),
            change: (diagnosis.change || "").trim(),
            mechanism: diagnosis.mechanism,
            edited: false,
            symptom: diagnosis.symptom,
          }
        : null,
    });

  return {
    ...fix,
    envs,
    main,
    mainState,
    drafts,
    allDrafts: scenarioSet.all,
    cantRun: runnableSet.cant,
    toolGap,
    inEnv,
    capped: scenarioSet.capped,
    limits,
    estimate,
    budgets,
    consentNeeded,
    building,
    ready,
    startFix,
  };
}

/* Reproduce only: the issue's scenarios added to the environment (the same
   write as "Add to environment"), so a normal run can run them. */
export const addIssueScenariosPatch = (envState, drafts, issueId) => {
  const have = new Set((envState?.scenarios || []).map((s) => s.id));
  const at = new Date().toISOString();
  const batchId = defaultBatchId("error-feed", at);
  const added = drafts
    .filter((d) => !have.has(d.id))
    .map((d) => ({
      ...stampProvenance(d, { source: "error-feed", at, batchId }),
      batchNote: `from ${issueId}`,
    }));
  if (!added.length) return null;
  return suiteLogPatch(
    envState,
    { scenarios: [...(envState?.scenarios || []), ...added] },
    {
      summary: `Added ${added.length} scenario${added.length === 1 ? "" : "s"} from ${issueId}`,
      by: currentUser().name,
    },
  );
};
