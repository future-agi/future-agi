import { useMemo } from "react";

import { useExtractAllVariables } from "./use-extract-all-variables";
import { normalizeForComparison } from "../Playground/common";

export const useIsVariablesDefined = (prompts, variableData, templateFormat) => {
  const variables = useExtractAllVariables(prompts, templateFormat);

  return useMemo(() => {
    // Create a Set of normalized keys for fast lookup
    const normalizedKeys = new Set(
      Object.keys(variableData || {}).map(normalizeForComparison),
    );

    // Check if all variables have corresponding data
    return variables.every((variable) =>
      normalizedKeys.has(normalizeForComparison(variable)),
    );
  }, [variableData, variables]);
};
