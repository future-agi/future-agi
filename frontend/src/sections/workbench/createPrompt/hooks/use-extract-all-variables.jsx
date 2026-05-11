import { useMemo } from "react";

import { extractVariables, normalizeForComparison } from "../Playground/common";

export const useExtractAllVariables = (prompts, templateFormat) => {
  // Use memoization to avoid re-running this expensive operation unless prompts change
  return useMemo(() => {
    // Use Map to store unique variables while preserving original formatting
    // Keys are normalized versions (for deduplication), values are original formatting
    const variableMap = new Map();

    // Iterate through each prompt version
    prompts.forEach(({ prompts }) => {
      // For each version, extract variables from all non-assistant messages
      const extractedForVersion = prompts.reduce((acc, { content, _ }) => {
        // Skip assistant messages since they don't contain input variables
        // if (role === PromptRoles.ASSISTANT) {
        //   return acc;
        // }
        // Combine existing variables with newly extracted ones
        // extractVariables finds all text within {{ }} in the content
        return [...acc, ...extractVariables(content, templateFormat)];
      }, []);

      // Process each extracted variable
      extractedForVersion.forEach((v) => {
        // Normalize the variable for comparison (handles whitespace/special chars)
        const normalized = normalizeForComparison(v);
        // Only add non-empty variables that haven't been seen before
        if (normalized.length > 0 && !variableMap.has(normalized)) {
          // Store original variable format as the value, using normalized as key
          variableMap.set(normalized, v);
        }
      });
    });

    // Convert Map values to array, giving us deduplicated variables with original formatting
    return Array.from(variableMap.values());
  }, [prompts, templateFormat]);
};
