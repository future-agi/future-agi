// The trace a trajectory shows: the same trace-detail query TraceTab and
// TraceDetailDrawerV2 use (`useGetTraceDetail`), never a second fetch.
// `null` traceId → disabled (no request).
import { useMemo } from "react";
import { useGetTraceDetail } from "src/api/project/trace-detail";

const spanOf = (entry) => entry?.observation_span ?? {};

export default function useTraceTree(traceId) {
  const q = useGetTraceDetail(traceId ?? null);
  const spans = q.data?.observation_spans ?? null;
  const rootId = useMemo(
    () => (spans?.length ? spanOf(spans[0]).id ?? null : null),
    [spans],
  );
  return {
    data: q.data ?? null,
    spans,
    rootId,
    isLoading: Boolean(traceId) && q.isLoading,
    isError: q.isError,
    error: q.error,
    refetch: q.refetch,
  };
}

/** The tree entry of a span id (depth first), or null. */
export function findEntry(entries, id) {
  if (!id) return null;
  for (const entry of entries ?? []) {
    if (spanOf(entry).id === id) return entry;
    const hit = findEntry(entry.children, id);
    if (hit) return hit;
  }
  return null;
}
