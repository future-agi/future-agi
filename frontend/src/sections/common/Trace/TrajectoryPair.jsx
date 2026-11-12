// One trace, or two traces of the same task, on the ONE trajectory UI
// (D11): RunTrajectory (Observe's TraceTreeV2 / AgentGraph /
// SpanTreeTimeline / SpanDetailPane). The R3 props stay for consumers
// (Try on 1 row, TraceCompareDrawer): `left` / `right` sides, an optional
// pair `picker`, a `notice` line and a `footer`. The R3 two-line banner is
// RunTrajectory's compare header, still G0's `First difference: step 2 ·
// lookup_order input changed (…)` (alignPairMilestones → firstDifferenceText).
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { useRunColors } from "src/sections/common/Runs/runColors";
import RunTrajectory from "src/sections/common/Trajectory/RunTrajectory";

const sideShape = PropTypes.shape({
  traceId: PropTypes.string,
  label: PropTypes.string,
  runId: PropTypes.string,
  source: PropTypes.string,
  kind: PropTypes.string,
  run: PropTypes.object,
});

const TrajectoryPair = ({
  left,
  right,
  footer,
  picker,
  notice,
  height = 440,
}) => {
  const colors = useRunColors(
    [left?.runId, right?.runId].filter(Boolean),
    left?.runId,
  );
  return (
    <Stack spacing={1}>
      {picker}
      {notice}
      <Box
        sx={{
          height,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <RunTrajectory
          key={`${left?.traceId}|${right?.traceId}`}
          traceId={left?.traceId ?? null}
          runId={left?.runId}
          run={left?.run ?? (left?.kind ? { kind: left.kind } : undefined)}
          label={left?.label}
          compareTraceId={right?.traceId ?? undefined}
          compareRunId={right?.runId}
          compareRun={
            right?.run ?? (right?.kind ? { kind: right.kind } : undefined)
          }
          compareLabel={right?.label}
          colors={{
            a: colors[left?.runId]?.solid,
            b: colors[right?.runId]?.solid,
          }}
          urlState={false}
          keys={false}
        />
      </Box>
      {footer}
    </Stack>
  );
};

TrajectoryPair.propTypes = {
  left: sideShape,
  right: sideShape,
  hideMatching: PropTypes.bool,
  footer: PropTypes.node,
  picker: PropTypes.node,
  notice: PropTypes.node,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

export default TrajectoryPair;
