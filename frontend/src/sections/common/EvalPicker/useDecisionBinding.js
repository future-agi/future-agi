import { useMemo } from "react";
import { CAPABILITY, useFeatureDenied } from "src/hooks/useCapabilities";
import useDecisionModels from "src/api/evaluations/decision-models";
import {
  findDecisionGroup,
  isDecisionModel,
  isJevDecisionModel,
} from "src/sections/evals/utils/decisionModels";
import {
  buildJevMapping,
  getJevValidationErrors,
} from "src/sections/evals/utils/jevMapping";

export const normalizeDecisionOutput = (output) =>
  ({
    "Pass/Fail": "pass_fail",
    score: "percentage",
    choices: "deterministic",
  })[output] ||
  output ||
  "pass_fail";

export default function useDecisionBinding({
  model,
  evalType,
  outputType,
  choiceScores,
  mapping,
  config = {},
  multiChoice,
  messages,
  inputDataTypes,
  isComposite = false,
}) {
  const active = !isComposite && evalType !== "code" && isDecisionModel(model);
  const catalog = useDecisionModels(active);
  const jevDenied = useFeatureDenied(CAPABILITY.JEV_MODELS);
  const group = findDecisionGroup(catalog.data || [], model);
  const label = isJevDecisionModel(model)
    ? "Jev"
    : group?.label || "This decision model";
  const locked = active && isJevDecisionModel(model) && jevDenied;
  const typedMapping = useMemo(
    () =>
      active
        ? buildJevMapping(
            normalizeDecisionOutput(outputType),
            choiceScores,
            mapping,
          )
        : undefined,
    [active, outputType, choiceScores, mapping],
  );
  const payload = useMemo(
    () => (active ? { jev_mapping: typedMapping } : {}),
    [active, typedMapping],
  );
  const errors = active
    ? getJevValidationErrors({
        mapping: typedMapping,
        config,
        choiceScores,
        multiChoice,
        messages,
        inputDataTypes,
        modelLabel: label,
        modelGroup: group,
      })
    : [];
  return {
    active,
    label,
    locked,
    errors,
    payload,
    blockedReason:
      errors[0] || (locked ? "Jev models aren't included in your plan." : ""),
  };
}
