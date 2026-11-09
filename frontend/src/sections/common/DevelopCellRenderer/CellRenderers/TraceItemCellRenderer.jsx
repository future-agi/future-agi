import React, { useMemo } from "react";
import { Box } from "@mui/material";
import TraceChip from "src/sections/common/Trace/TraceChip";
import { useSourceTraceStore } from "src/sections/develop-detail/DataTab/DataGrid/SystemCells";
import TextArrayJsonCellRenderer from "./TextArrayJsonCellRenderer";
import { commonPropTypes } from "./cellRendererHelper";
import { traceItemSummary } from "src/sections/develop-detail/TaskSuite/itemModel";

/**
 * `trace` cells (R2-SPEC §7.3.6): `support-agent · 9 spans · 2.1 s · OK` as
 * a trace chip; a click opens the frozen trace in the trace drawer.
 *
 * Props (frozen): commonPropTypes + `isHover`, `cellData`.
 */
const TraceItemCellRenderer = (props) => {
  const { value } = props;
  const openTrace = useSourceTraceStore((s) => s.open);
  const summary = useMemo(() => traceItemSummary(value), [value]);
  if (!summary) return <TextArrayJsonCellRenderer {...props} />;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        height: "100%",
        px: 1,
        minWidth: 0,
      }}
      data-testid="trace-cell"
    >
      <TraceChip
        state={summary.status === "error" ? "error" : "ok"}
        label={summary.text}
        tooltip={
          summary.traceId
            ? `Open the trace saved at import · ${summary.traceId}`
            : "No trace id on this value"
        }
        onOpen={
          summary.traceId
            ? () =>
                openTrace({
                  traceId: summary.traceId,
                  projectId: summary.projectId,
                })
            : undefined
        }
        compact
      />
    </Box>
  );
};

TraceItemCellRenderer.propTypes = commonPropTypes;

export default TraceItemCellRenderer;
