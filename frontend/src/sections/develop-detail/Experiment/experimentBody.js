// The DSX create / edit body of the New experiment wizard (pure), moved out
// of RunExperiment.jsx (L2) so the contract test builds exactly the body the
// form sends. serverBody.js turns it into what dev reads, or blocks Run.
import {
  experimentExtras,
  seedFirstResultOf,
  startFromPayload,
  withPromptTools,
} from "./experimentFormValues";
import { withEndpointLimits } from "./estimateInputs";
import { flattenRuns } from "./runPlan";
import { promptConfigTransform } from "./utils";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { withoutExperimentExecutionPin } from "src/launch/experimentExecutionPin";

/**
 * @param {object} data  the validated form values (zod-transformed: snake
 *   case prompt_config entries in `promptConfig`, wire eval metrics in
 *   `userEvalMetrics`)
 * @param {object} raw   getValues(): the form as the user left it
 * @param {{isEditing?: boolean, datasetId?: string}} opts
 */
export function experimentBody(
  data,
  raw,
  { isEditing = false, datasetId } = {},
) {
  const runs = flattenRuns(raw?.promptConfig ?? []);
  const body = {
    ...(!isEditing && {
      dataset_id: datasetId,
      experiment_type: data?.experimentType,
    }),
    ...experimentExtras(raw, { isEditing }),
    ...(!isEditing && startFromPayload(raw)),
    prompt_config: withEndpointLimits(
      withPromptTools(data?.promptConfig, raw?.promptConfig, runs),
      raw?.promptConfig,
      runs,
    ),
    user_eval_metrics:
      !isEditing && !isSurfaceShown("exp.new")
        ? data?.userEvalMetrics?.map(withoutExperimentExecutionPin)
        : data?.userEvalMetrics,
  };
  // L5 G3: a create always says its reference column (null = None, so
  // reference evals read `Skipped · no reference column`).
  if (!isEditing) body.column_id = data?.columnId ?? null;
  else if (data?.columnId) body.column_id = data.columnId;
  // IT-R5 G1 P1: the try the wizard simulated is the run's first result.
  const seed = !isEditing
    ? (raw?.promptConfig ?? [])
        .map((item) => seedFirstResultOf(item?.r4))
        .find(Boolean)
    : null;
  if (seed) body.seed_first_result = seed;
  return body;
}

/**
 * The body of the form as it stands, before validation (for the live server
 * blocks): run entries through the same transform the schema applies; the
 * eval metrics are left out (every key the wizard sends for one is a key
 * dev declares).
 */
export function experimentBodyOfValues(
  raw,
  { isEditing = false, datasetId } = {},
) {
  let promptConfig = [];
  try {
    promptConfig = promptConfigTransform(
      raw?.promptConfig ?? [],
      raw?.experimentType ?? "llm",
      raw?.outputFormat,
      isEditing,
    );
  } catch {
    promptConfig = [];
  }
  return experimentBody(
    {
      experimentType: raw?.experimentType ?? "llm",
      promptConfig,
      userEvalMetrics: [],
      columnId: raw?.columnId || null,
    },
    raw,
    { isEditing, datasetId },
  );
}
