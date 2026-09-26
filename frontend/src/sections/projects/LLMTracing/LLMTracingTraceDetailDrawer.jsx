import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { useLLMTracingStoreShallow } from "./states";
import { useParams } from "react-router";

const LLMTracingTraceDetailDrawer = ({ refreshGrid }) => {
  const { observeId } = useParams();
  const { traceDetailDrawerOpen, setTraceDetailDrawerOpen, visibleTraces } =
    useLLMTracingStoreShallow((state) => ({
      traceDetailDrawerOpen: state.traceDetailDrawerOpen,
      setTraceDetailDrawerOpen: state.setTraceDetailDrawerOpen,
      visibleTraces: state.visibleTraces,
    }));

  const traceId = traceDetailDrawerOpen?.traceId || null;
  // The clicked row's project. /dashboard/users/:userId has no route
  // project and lists every project's rows; a trace id is not unique across
  // projects, so an unpinned read could open another project's copy. A
  // project route stays authoritative.
  const pinnedProjectId = traceDetailDrawerOpen?.projectId || undefined;
  const filters = traceDetailDrawerOpen?.filters || [];

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
      setTraceDetailDrawerOpen({
        traceId: next.traceId,
        ...(next.projectId ? { projectId: next.projectId } : {}),
        filters,
      });
    },
    [currentIdx, visibleTraces, filters, setTraceDetailDrawerOpen],
  );

  const onPrev = useCallback(() => navigateToTrace(-1), [navigateToTrace]);
  const onNext = useCallback(() => navigateToTrace(1), [navigateToTrace]);

  return (
    <TraceDetailDrawerV2
      traceId={traceId}
      open={Boolean(traceDetailDrawerOpen)}
      onClose={() => setTraceDetailDrawerOpen(null)}
      projectId={observeId || pinnedProjectId}
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
