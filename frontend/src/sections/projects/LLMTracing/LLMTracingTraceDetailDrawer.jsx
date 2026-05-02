import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useLLMTracingStoreShallow } from "./states";
import { useParams } from "react-router";

const LLMTracingTraceDetailDrawer = ({ refreshGrid }) => {
  const { observeId } = useParams();
  const { traceDetailDrawerOpen, setTraceDetailDrawerOpen, visibleTraceIds } =
    useLLMTracingStoreShallow((state) => ({
      traceDetailDrawerOpen: state.traceDetailDrawerOpen,
      setTraceDetailDrawerOpen: state.setTraceDetailDrawerOpen,
      visibleTraceIds: state.visibleTraceIds,
    }));

  const traceId = traceDetailDrawerOpen?.traceId || null;
  const filters = traceDetailDrawerOpen?.filters || [];

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
      setTraceDetailDrawerOpen({
        traceId: visibleTraceIds[nextIdx],
        filters,
      });
    },
    [currentIdx, visibleTraceIds, filters, setTraceDetailDrawerOpen],
  );

  const onPrev = useCallback(() => navigateToTrace(-1), [navigateToTrace]);
  const onNext = useCallback(() => navigateToTrace(1), [navigateToTrace]);

  return (
    <TraceDetailDrawerV2
      traceId={traceId}
      open={Boolean(traceDetailDrawerOpen)}
      onClose={() => setTraceDetailDrawerOpen(null)}
      projectId={observeId}
      onPrev={onPrev}
      onNext={onNext}
      hasPrev={hasPrev}
      hasNext={hasNext}
      refreshParentGrid={refreshGrid}
    />
  );
};

LLMTracingTraceDetailDrawer.propTypes = {
  refreshGrid: PropTypes.func,
};

export default LLMTracingTraceDetailDrawer;
