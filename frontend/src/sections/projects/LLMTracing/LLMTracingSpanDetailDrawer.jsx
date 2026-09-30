import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useParams } from "react-router";
import { useLLMTracingStoreShallow } from "./states";

const LLMTracingSpanDetailDrawer = ({ refreshGrid }) => {
  const { observeId } = useParams();
  const { spanDetailDrawerOpen, setSpanDetailDrawerOpen, visibleTraces } =
    useLLMTracingStoreShallow((state) => ({
      spanDetailDrawerOpen: state.spanDetailDrawerOpen,
      setSpanDetailDrawerOpen: state.setSpanDetailDrawerOpen,
      visibleTraces: state.visibleTraces,
    }));

  const traceId = spanDetailDrawerOpen?.trace_id || null;
  const spanId = spanDetailDrawerOpen?.span_id || null;
  // The clicked span's project, for routes without one (see
  // LLMTracingTraceDetailDrawer).
  const pinnedProjectId = spanDetailDrawerOpen?.project_id || undefined;

  const currentIdx = useMemo(
    () =>
      traceId
        ? visibleTraces.findIndex(
            (row) =>
              row.traceId === traceId &&
              (!pinnedProjectId || row.projectId === pinnedProjectId),
          )
        : -1,
    [traceId, pinnedProjectId, visibleTraces],
  );
  const hasPrev = currentIdx > 0;
  const hasNext = currentIdx >= 0 && currentIdx < visibleTraces.length - 1;

  const navigateToTrace = useCallback(
    (direction) => {
      if (currentIdx === -1) return;
      const nextIdx = currentIdx + direction;
      if (nextIdx < 0 || nextIdx >= visibleTraces.length) return;
      const next = visibleTraces[nextIdx];
      setSpanDetailDrawerOpen({
        ...spanDetailDrawerOpen,
        trace_id: next.traceId,
        project_id: next.projectId || undefined,
        // Drop pinned span when navigating to adjacent trace — no way to
        // know what the equivalent span would be in the next trace.
        span_id: null,
      });
    },
    [currentIdx, visibleTraces, spanDetailDrawerOpen, setSpanDetailDrawerOpen],
  );

  const onPrev = useCallback(() => navigateToTrace(-1), [navigateToTrace]);
  const onNext = useCallback(() => navigateToTrace(1), [navigateToTrace]);

  return (
    <TraceDetailDrawerV2
      traceId={traceId}
      open={Boolean(spanDetailDrawerOpen)}
      onClose={() => setSpanDetailDrawerOpen(null)}
      projectId={observeId || pinnedProjectId}
      initialSpanId={spanId}
      onPrev={onPrev}
      onNext={onNext}
      hasPrev={hasPrev}
      hasNext={hasNext}
    />
  );
};

LLMTracingSpanDetailDrawer.propTypes = {
  refreshGrid: PropTypes.func,
};

export default LLMTracingSpanDetailDrawer;
