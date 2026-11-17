import { LLM_TABS } from "../../../sections/projects/LLMTracing/common";

/**
 * What a trace-drawer import selects, and the kind of row source it records:
 * a trace on the trace tab, a span otherwise. The server takes each row's
 * trace, span and session from the row's own span; the kind never changes
 * which rows import.
 */
export function importSelection({
  currentTab,
  selectedTraces,
  selectedSpans,
  selectedNode,
}) {
  return currentTab === LLM_TABS.TRACE
    ? { source_kind: "trace", trace_ids: selectedTraces }
    : {
        source_kind: "span",
        span_ids: selectedNode ? [selectedNode] : selectedSpans,
      };
}
