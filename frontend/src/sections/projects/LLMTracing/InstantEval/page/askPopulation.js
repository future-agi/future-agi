import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { collectPopulation } from "src/sections/projects/observePopulation";
import {
  DEFAULT_LIMIT,
  LIMIT_OPTIONS,
  populationCount,
} from "../instantEvalUnits";

// The smallest run size that takes every row in view, or the largest one.
export function autoLimit(choices, count, unit) {
  if (!count) return DEFAULT_LIMIT[unit] || DEFAULT_LIMIT.traces;
  return choices.find((value) => value >= count) || Math.max(...choices);
}

/**
 * The rows a new question is about: the rows handed over from the view, or
 * the newest rows its filters match. Reading up to the largest run size
 * proves how many a run can take.
 */
export function useAskPopulation({ projectId, unit, seed }) {
  const maxLimit = Math.max(...(LIMIT_OPTIONS[unit] || [500]));
  const handed = useMemo(() => {
    if (Array.isArray(seed?.population) && seed.population.length)
      return seed.population.filter((item) => item?.id);
    if (Array.isArray(seed?.ids) && seed.ids.length)
      return seed.ids.filter(Boolean).map((id) => ({ id }));
    return null;
  }, [seed]);
  const filters = useMemo(() => seed?.filters || [], [seed]);
  const query = useQuery({
    queryKey: ["ask-population", projectId, unit, JSON.stringify(filters)],
    enabled: Boolean(projectId) && !handed,
    queryFn: async ({ signal }) => ({
      ...(await collectPopulation({
        unit,
        projectId,
        filters,
        limit: maxLimit,
        signal,
      })),
      limit: maxLimit,
    }),
    staleTime: 60_000,
    meta: { errorHandled: true },
  });
  if (handed)
    return {
      items: handed,
      count: { count: handed.length, lowerBound: false },
      handedOver: true,
      isLoading: false,
      isError: false,
      refetch: () => {},
    };
  return {
    items: query.data?.items || [],
    count: populationCount(query.data),
    handedOver: false,
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
  };
}
