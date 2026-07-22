import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// Internal: shared queries hook so callers can derive both the union of
// required keys AND per-child schema (function_params_schema +
// config_params_desc) without firing duplicate requests.
function useCompositeChildrenDetailResults(children) {
  const childIds = useMemo(
    () => (children || []).map((c) => c?.child_id).filter(Boolean),
    [children],
  );

  return useQueries({
    queries: childIds.map((id) => ({
      queryKey: ["evals", "detail", id],
      queryFn: async () => {
        const { data } = await axios.get(
          endpoints.develop.eval.getEvalDetail(id),
        );
        return data?.result;
      },
      enabled: !!id,
    })),
  });
}

// Fetches every child template referenced by the composite editor and
// returns the *union* of their `required_keys` — the full variable set
// the composite will need mapped when it's bound to a dataset.
//
// Uses react-query's `useQueries` so the number of fetches scales with
// the number of children without violating the rules of hooks. All
// requests share the `["evals","detail",templateId]` cache with
// `useEvalDetail` so there's no duplicate network cost.
export function useCompositeChildrenUnionKeys(children = []) {
  const results = useCompositeChildrenDetailResults(children);

  return useMemo(() => {
    const union = new Set();
    results.forEach((q) => {
      const r = q?.data;
      const keys =
        r?.required_keys ||
        r?.config?.required_keys ||
        r?.config?.requiredKeys ||
        [];
      keys.forEach((k) => union.add(k));
    });
    return [...union];
  }, [results]);
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
