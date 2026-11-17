import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  TextField,
  Typography,
} from "@mui/material";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import ContentSkeleton from "src/components/content-skeleton";
import SpanDetailPane from "./SpanDetailPane";
import TraceTreeV2 from "./TraceTreeV2";
import { CaptureCoverageNotice } from "./TraceDetailPanel";

function findSpan(entries, spanId) {
  for (const entry of entries || []) {
    if (entry.observation_span?.id === spanId) return entry;
    const child = findSpan(entry.children, spanId);
    if (child) return child;
  }
  return null;
}

function TraceCompareColumn({ label, traceId, data, projectId, initialSpanId }) {
  const spans = data.observation_spans;
  const root = spans?.find((entry) => !entry.observation_span?.parent_span_id);
  const rootSpanId = root?.observation_span?.id;
  const [selectedSpanId, setSelectedSpanId] = useState(initialSpanId || rootSpanId);

  useEffect(() => {
    setSelectedSpanId(initialSpanId || rootSpanId);
  }, [initialSpanId, rootSpanId]);

  const selectedEntry = useMemo(
    () => findSpan(spans, selectedSpanId),
    [spans, selectedSpanId],
  );

  return (
    <Box
      component="section"
      aria-label={label}
      sx={{
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        border: "1px solid",
        borderColor: "divider",
        overflow: "hidden",
      }}
    >
      <Box sx={{ px: 2, py: 1, bgcolor: "background.default" }}>
        <Typography variant="subtitle2">{label}</Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {traceId}
        </Typography>
      </Box>
      <CaptureCoverageNotice coverage={data.capture_coverage} />
      <Box
        sx={{
          maxHeight: "35%",
          minHeight: 120,
          overflow: "auto",
          flexShrink: 0,
        }}
      >
        <TraceTreeV2
          spans={spans}
          selectedSpanId={selectedSpanId}
          onSelectSpan={setSelectedSpanId}
        />
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {selectedEntry ? (
          <SpanDetailPane
            entry={selectedEntry}
            allSpans={spans}
            traceStartTime={root?.observation_span?.start_time}
            isRootSpan={selectedSpanId === rootSpanId}
            traceTags={data.trace?.tags || []}
            projectId={projectId}
            onClose={() => setSelectedSpanId(null)}
            onSelectSpan={setSelectedSpanId}
            readOnly
          />
        ) : (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            Select a span to view details
          </Typography>
        )}
      </Box>
    </Box>
  );
}

TraceCompareColumn.propTypes = {
  label: PropTypes.string.isRequired,
  traceId: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  projectId: PropTypes.string.isRequired,
  initialSpanId: PropTypes.string,
};

export default function TraceCompareView({
  traceId,
  data,
  projectId,
  initialSpanId,
}) {
  const [draftTraceId, setDraftTraceId] = useState("");
  const [comparisonTraceId, setComparisonTraceId] = useState(null);
  const trimmedTraceId = draftTraceId.trim();
  const sameTrace = trimmedTraceId === traceId;
  const {
    data: comparisonData,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = useGetTraceDetail(comparisonTraceId, { projectId });
  const accessDenied = error?.statusCode === 401 || error?.statusCode === 403;

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmedTraceId && !sameTrace) setComparisonTraceId(trimmedTraceId);
        }}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 1,
          p: 2,
        }}
      >
        <TextField
          autoFocus
          label="Trace ID to compare"
          value={draftTraceId}
          onChange={(event) => setDraftTraceId(event.target.value)}
          size="small"
          error={sameTrace}
          helperText={
            sameTrace
              ? "Choose a different trace ID."
              : "Enter another trace ID from this project."
          }
          sx={{ flex: 1, minWidth: 200 }}
        />
        <Button
          type="submit"
          variant="outlined"
          disabled={!trimmedTraceId || sameTrace}
        >
          Load trace
        </Button>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            md: "repeat(2, minmax(0, 1fr))",
          },
          gridAutoRows: { xs: "minmax(480px, 1fr)", md: "minmax(0, 1fr)" },
          overflow: "auto",
          gap: 1,
          px: 2,
          pb: 2,
        }}
      >
        <TraceCompareColumn
          key={traceId}
          label="Current trace"
          traceId={traceId}
          data={data}
          projectId={projectId}
          initialSpanId={initialSpanId}
        />
        {!comparisonTraceId ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            Choose a trace to compare its spans, inputs, outputs and metrics.
          </Typography>
        ) : isError ? (
          <Box sx={{ minWidth: 0 }}>
            <Alert
              severity="error"
              action={
                !accessDenied && (
                  <Button
                    color="inherit"
                    size="small"
                    onClick={() => refetch()}
                    disabled={isFetching}
                  >
                    Retry comparison
                  </Button>
                )
              }
            >
              <AlertTitle>Unable to load comparison trace</AlertTitle>
              {error?.statusCode === 401
                ? "Sign in again to view this trace."
                : error?.statusCode === 403
                  ? "You do not have permission to view this trace. Choose a trace you can access."
                  : "Choose a trace from this project, or retry if the trace service is unavailable."}
            </Alert>
          </Box>
        ) : isLoading ? (
          <Box sx={{ p: 2 }}>
            <Typography role="status" variant="body2">
              Loading comparison trace…
            </Typography>
            <ContentSkeleton variant="panel" />
          </Box>
        ) : comparisonData ? (
          <TraceCompareColumn
            key={comparisonTraceId}
            label="Comparison trace"
            traceId={comparisonTraceId}
            data={comparisonData}
            projectId={projectId}
          />
        ) : (
          <Alert severity="info" sx={{ alignSelf: "start" }}>
            No trace data was returned. Choose another trace from this project.
          </Alert>
        )}
      </Box>
    </Box>
  );
}

TraceCompareView.propTypes = {
  traceId: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  projectId: PropTypes.string.isRequired,
  initialSpanId: PropTypes.string,
};
