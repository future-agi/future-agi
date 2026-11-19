import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { extractCodeEvaluateParams } from "src/utils/codeEvalParams";
import {
  getEvalCode,
  getEvalCodeLanguage,
  getEvalRequiredKeys,
  getEvalType,
} from "src/sections/common/EvalPicker/evalPickerValue";
import { evalDetailQuery } from "./useEvalDetail";

// Internal: shared queries hook so callers can derive both the union of
// required keys AND per-child schema (function_params_schema +
// config_params_desc) without firing duplicate requests.
function useCompositeChildrenDetailResults(children, combine) {
  const childIds = useMemo(
    () => (children || []).map((c) => c?.child_id).filter(Boolean),
    [children],
  );

  return useQueries({
    combine,
    queries: childIds.map((id) => ({
      ...evalDetailQuery(id),
      enabled: !!id,
    })),
  });
}

function combineChildInputs(results) {
  const keys = new Set();
  for (const { data } of results) {
    if (!data) continue;
    // Custom code templates store required_keys: []; the individual picker
    // gets their mappable inputs from evaluate(...). System wrappers have a
    // generic signature, so only their declared keys are meaningful.
    if (getEvalType(data) === "code" && data.owner !== "system") {
      const params = extractCodeEvaluateParams(
        getEvalCode(data),
        getEvalCodeLanguage(data),
      );
      for (const key of params) keys.add(key);
    }
    for (const key of getEvalRequiredKeys(data)) keys.add(key);
  }
  return {
    keys: [...keys],
    isLoading: results.some((query) => query.isPending),
    isError: results.some(
      (query) => query.isError || (query.isSuccess && !query.data),
    ),
  };
}

export function useCompositeChildrenInputs(children = []) {
  return useCompositeChildrenDetailResults(children, combineChildInputs);
}

export function useCompositeChildrenUnionKeys(children = []) {
  return useCompositeChildrenInputs(children).keys;
}

// Returns a map keyed by child_id with the schema bits the composite
// editor needs to render per-child param inputs. Shares the same
// react-query cache as `useCompositeChildrenUnionKeys` so adding both
// hooks to one component costs a single round-trip per child.
export function useCompositeChildrenSchemas(children = []) {
  const childIds = useMemo(
    () => (children || []).map((c) => c?.child_id).filter(Boolean),
    [children],
  );
  const results = useCompositeChildrenDetailResults(children);

  return useMemo(() => {
    const map = {};
    childIds.forEach((id, idx) => {
      const r = results[idx]?.data;
      if (!r) return;
      const config = r.config || {};
      map[id] = {
        functionParamsSchema:
          config.function_params_schema ||
          config.functionParamsSchema ||
          r.function_params_schema ||
          null,
        configParamsDesc:
          config.config_params_desc ||
          config.configParamsDesc ||
          r.config_params_desc ||
          null,
        requiredKeys:
          r.required_keys || config.required_keys || config.requiredKeys || [],
      };
    });
    return map;
  }, [childIds, results]);
}
