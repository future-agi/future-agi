import { useCallback, useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { taskCodeInputs } from "src/utils/codeEvalParams";
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

// Default for existing picker hosts: an explicitly empty top-level list wins
// over nested keys. Do not infer inputs from code signatures on these paths.
function combineDeclaredInputs(results) {
  const keys = new Set();
  for (const { data } of results) {
    const declared =
      data?.required_keys ||
      data?.config?.required_keys ||
      data?.config?.requiredKeys ||
      [];
    for (const key of declared) keys.add(key);
  }
  return { keys: [...keys] };
}

function combineTaskInputs(results, children) {
  const keys = new Set();
  const required = new Set();
  let unknown = false;
  results.forEach(({ data }, i) => {
    if (!data) return;
    const link = children[i];
    const bound = link.config?.params || {};
    const add = (name, needed) => {
      if (Object.hasOwn(bound, name)) return;
      keys.add(name);
      if (needed) required.add(name);
    };
    const declared = getEvalRequiredKeys(data);
    if (getEvalType(data) === "code" && data.owner !== "system") {
      const inputs = taskCodeInputs(getEvalCode(data), getEvalCodeLanguage(data));
      if (!inputs && !declared.length) unknown = true;
      for (const { name, required: needed } of inputs || []) add(name, needed);
    }
    for (const key of declared) add(key, true);
  });
  return {
    keys: [...keys],
    required: [...required],
    unknown,
    hasData: results.every((q) => Boolean(q.data)),
    isLoading: results.some((q) => q.isPending || q.isFetching || q.isPaused),
    isError: results.some((q) => q.isError || (q.isSuccess && !q.data)),
    retry: () => results.filter((q) => q.isError).forEach((q) => q.refetch()),
  };
}

export function useCompositeChildrenInputs(
  children = [],
  { taskComposite = false } = {},
) {
  const combine = useCallback(
    (results) => combineTaskInputs(results, children.filter((c) => c?.child_id)),
    [children],
  );
  return useCompositeChildrenDetailResults(
    children,
    taskComposite ? combine : combineDeclaredInputs,
  );
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
