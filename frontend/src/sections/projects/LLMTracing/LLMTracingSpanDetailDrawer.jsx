import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useParams } from "react-router";
import { useLLMTracingStoreShallow } from "./states";
import { useUrlState } from "src/routes/hooks/use-url-state";

const LLMTracingSpanDetailDrawer = ({ refreshGrid }) => {
  const { observeId } = useParams();
  const [, setDrawerTab] = useUrlState("drawerTab");
  const { spanDetailDrawerOpen, setSpanDetailDrawerOpen, visibleTraceIds } =
    useLLMTracingStoreShallow((state) => ({
      spanDetailDrawerOpen: state.spanDetailDrawerOpen,
      setSpanDetailDrawerOpen: state.setSpanDetailDrawerOpen,
      visibleTraceIds: state.visibleTraceIds,
    }));

  const traceId = spanDetailDrawerOpen?.trace_id || null;
  const spanId = spanDetailDrawerOpen?.span_id || null;

  const currentIdx = useMemo(
    () => (traceId ? visibleTraceIds.indexOf(traceId) : -1),
    [traceId, visibleTraceIds],
  );
  const hasPrev = currentIdx > 0;
  const hasNext = currentIdx >= 0 && currentIdx < visibleTraceIds.length - 1;

  const navigateToTrace = useCallback(
    (direction) => {
      if (currentIdx === -1) return;
      const nextIdx = currentIdx + direction;
      if (nextIdx < 0 || nextIdx >= visibleTraceIds.length) return;
      setSpanDetailDrawerOpen({
        ...spanDetailDrawerOpen,
        trace_id: visibleTraceIds[nextIdx],
        // Drop pinned span when navigating to adjacent trace — no way to
        // know what the equivalent span would be in the next trace.
        span_id: null,
      });
    },
    [
      currentIdx,
      visibleTraceIds,
      spanDetailDrawerOpen,
      setSpanDetailDrawerOpen,
    ],
  );

  const onPrev = useCallback(() => navigateToTrace(-1), [navigateToTrace]);
  const onNext = useCallback(() => navigateToTrace(1), [navigateToTrace]);

  return (
    <TraceDetailDrawerV2
      traceId={traceId}
      open={Boolean(spanDetailDrawerOpen)}
      onClose={() => {
        setDrawerTab(null);
        setSpanDetailDrawerOpen(null);
      }}
      projectId={observeId}
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
