import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useParams } from "react-router-dom";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useRowComments } from "src/api/develop/row-collab";
import CommentThreads from "./CommentThread";
import { stepAnchorOptions, traceSteps } from "./commentModel";

/**
 * W4-1 on a result row (row × run): "Comments on #4A's answer (1)", the
 * thread keyed (row, run), with "On step [▾]" listing the try's trace
 * steps (the existing trace read).
 */
const RunCommentsSection = ({
  datasetId: datasetIdProp,
  rowId,
  runId,
  runLabel,
  traceId,
}) => {
  // The dataset page's route names the dataset; the results drawer passes
  // it (W4 §9 ResultDrawer integration, M6).
  const { dataset: fromRoute } = useParams();
  const datasetId = datasetIdProp ?? fromRoute;
  const q = useRowComments(datasetId, rowId, { runId });
  const trace = useGetTraceDetail(traceId);
  const steps = useMemo(
    () => stepAnchorOptions(traceSteps(trace.data?.observation_spans), traceId),
    [trace.data, traceId],
  );
  if (!datasetId || !rowId || !runId) return null;
  const n = q.data?.threads?.length ?? 0;
  return (
    <Box sx={{ mt: 1.5 }} data-testid="run-comments">
      <Typography typography="s2" fontWeight={600} sx={{ mb: 0.75 }}>
        {`Comments on ${runLabel ?? "this run"}'s answer`}
        <Box component="span" sx={{ color: "text.disabled", fontWeight: 400 }}>
          {` (${n.toLocaleString("en-US")})`}
        </Box>
      </Typography>
      <CommentThreads
        datasetId={datasetId}
        rowId={rowId}
        runId={runId}
        status="all"
        anchorOptions={[
          { key: "row", label: "Whole answer", anchor: { kind: "row" } },
        ]}
        stepOptions={steps}
        emptyText="No comments on this answer yet."
      />
    </Box>
  );
};

RunCommentsSection.propTypes = {
  datasetId: PropTypes.string,
  rowId: PropTypes.string.isRequired,
  runId: PropTypes.string,
  runLabel: PropTypes.string,
  traceId: PropTypes.string,
};

export default RunCommentsSection;
