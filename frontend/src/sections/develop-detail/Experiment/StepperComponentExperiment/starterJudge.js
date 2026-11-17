// A ready judge for a first experiment (JRN-EXP-NO-PROMPT-DEADEND). Dev's V2
// create refuses an experiment with no evaluation
// (ExperimentCreateV2Serializer.validate_user_eval_metrics,
// model_hub/serializers/experiments.py:921, 987-992), so an empty
// Evaluations step offers this one instead of a dead end.
//
// It is the workspace's own LLM judge (create-v2, eval_type llm:
// model_hub/views/separate_evals.py:3122-3149) and reads two values every
// prompt or agent run has, so no dataset column has to be picked:
//   - input:  `prompt_chain`, the prompt the run sent with the row's values
//             filled in (model_hub/views/eval_runner.py:443, 586-640);
//   - output: `output`, the run's reply (eval_runner.py:568-583).
// Its judge is the model the run already uses, which the workspace can call:
// create-v2 stores it on the judge and its first version
// (separate_evals.py:3204, 3222), the card sends it as the metric's model
// (views/experiments.py:5103), and this evaluator calls it with the
// workspace's provider key or managed route
// (evaluations/engine/instance.py:511-548, 750). create-v2 refuses a name it
// already has (separate_evals.py:2907-2922), so a later first experiment
// uses the judge an earlier one made, with the model saved on it.
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { PROMPT_CONFIG_TYPE } from "../constants";
import { targetTypeOf } from "../runPlan";

export const STARTER_JUDGE = Object.freeze({
  // create-v2 names: lowercase letters, digits, - and _.
  name: "answers_the_request",
  displayName: "Answers the request",
  instructions: [
    "Request sent to the model:",
    "{{input}}",
    "",
    "Reply:",
    "{{output}}",
    "",
    "Pass if the reply answers the request directly and completely. Fail if it refuses, goes off topic, leaves part of the request unanswered, or states facts the request does not support.",
  ].join("\n"),
  description:
    "Answers the request: passes a reply that answers what the run was asked.",
});

/** input: the prompt each run sent; output: its reply. */
export const STARTER_JUDGE_MAPPING = Object.freeze({
  input: "prompt_chain",
  output: "output",
});
const KEYS = Object.keys(STARTER_JUDGE_MAPPING);

/**
 * The judge model: the first model of the first text prompt run, or null
 * (agent, endpoint and media runs name no model the judge can use).
 */
export function starterJudgeModelOf(promptConfig) {
  for (const card of promptConfig ?? []) {
    if ((card?.experimentType ?? "llm") !== "llm") continue;
    if (targetTypeOf(card) !== PROMPT_CONFIG_TYPE.PROMPT) continue;
    const hit = (card?.model ?? []).find((m) => String(m?.value ?? "").trim());
    if (hit) return String(hit.value).trim();
  }
  return null;
}

/**
 * The judge's template id: made now, or the one an earlier first experiment
 * made. Any other refusal is thrown as it came.
 */
export async function ensureStarterJudge(model) {
  try {
    const res = await axios.post(endpoints.develop.eval.createEvalTemplateV2, {
      name: STARTER_JUDGE.name,
      eval_type: "llm",
      instructions: STARTER_JUDGE.instructions,
      description: STARTER_JUDGE.description,
      output_type: "pass_fail",
      model,
    });
    const id = res?.data?.result?.id;
    if (id) return id;
    throw new Error("The judge was not created. Try again.");
  } catch (error) {
    if (!/already exists/i.test(getRequestErrorMessage(error, ""))) throw error;
  }
  const res = await axios.post(endpoints.develop.eval.listEvalTemplates, {
    page: 0,
    page_size: 25,
    search: STARTER_JUDGE.name,
    owner_filter: "user",
  });
  const found = (res?.data?.result?.items ?? []).find(
    (t) => t?.name === STARTER_JUDGE.name,
  );
  if (found?.id) return found.id;
  throw new Error(
    `An evaluation named ${STARTER_JUDGE.name} already exists. Add it with Add evaluation.`,
  );
}

/** The judge as the step adds it (EvaluationStepExperimentCreation handleAddEvaluation). */
export const starterEvalConfig = ({ templateId, model }) => ({
  templateId,
  name: STARTER_JUDGE.name,
  description: STARTER_JUDGE.description,
  mapping: { ...STARTER_JUDGE_MAPPING },
  requiredKeys: [...KEYS],
  model,
  evalType: "llm",
  config: {},
  evalTemplate: { output_type: "Pass/Fail", requiredKeys: [...KEYS] },
});
