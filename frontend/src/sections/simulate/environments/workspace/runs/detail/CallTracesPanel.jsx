import { useEffect } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import { fDateTime } from "src/utils/format-time";
import { formatLatency } from "src/sections/projects/LLMTracing/formatters";
import { useLLMTracingStoreShallow } from "src/sections/projects/LLMTracing/states";
import LLMTracingTraceDetailDrawer from "src/sections/projects/LLMTracing/LLMTracingTraceDetailDrawer";
import EmptyState from "../../../components/EmptyState";
import { CALL_TRACES_COPY } from "./callTraces.constants";

const MONO = "ui-monospace, Menlo, monospace";

export default function CallTracesPanel({
  traces,
  projectId,
  testExecutionId,
  isError = false,
  onMountedChange,
}) {
  const { setTraceDetailDrawerOpen, setVisibleTraces, resetStates } =
    useLLMTracingStoreShallow((state) => ({
      setTraceDetailDrawerOpen: state.setTraceDetailDrawerOpen,
      setVisibleTraces: state.setVisibleTraces,
      resetStates: state.resetStates,
    }));

  useEffect(() => {
    setVisibleTraces(
      traces.map((trace) => ({ traceId: trace.traceId, projectId })),
    );
  }, [traces, projectId, setVisibleTraces]);

  useEffect(() => {
    onMountedChange?.(true);
    return () => {
      onMountedChange?.(false);
      // resetStates leaves the URL key behind; the setter removes it.
      setTraceDetailDrawerOpen(null);
      resetStates();
    };
  }, [onMountedChange, setTraceDetailDrawerOpen, resetStates]);

  if (isError && traces.length === 0) {
    return (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title={CALL_TRACES_COPY.error}
      />
    );
  }

  return (
    <Stack sx={{ minHeight: 0, flex: 1 }}>
      {traces.length === 0 ? (
        <EmptyState
          icon="solar:graph-new-linear"
          title={CALL_TRACES_COPY.empty}
        />
      ) : (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {traces.map((trace) => (
            <TraceRow
              key={trace.traceId}
              trace={trace}
              onOpen={() =>
                setTraceDetailDrawerOpen({
                  traceId: trace.traceId,
                  projectId,
                  testExecutionId,
                })
              }
            />
          ))}
        </Stack>
      )}
      <LLMTracingTraceDetailDrawer readOnly />
    </Stack>
  );
}
CallTracesPanel.propTypes = {
  traces: PropTypes.arrayOf(
    PropTypes.shape({
      traceId: PropTypes.string.isRequired,
      name: PropTypes.string,
      startTime: PropTypes.string,
      status: PropTypes.string,
      latencyMs: PropTypes.number,
    }),
  ).isRequired,
  projectId: PropTypes.string,
  testExecutionId: PropTypes.string,
  isError: PropTypes.bool,
  onMountedChange: PropTypes.func,
};

function TraceRow({ trace, onOpen }) {
  const hasRoot = trace.name != null;
  return (
    <ButtonBase
      onClick={onOpen}
      sx={{
        width: "100%",
        display: "block",
        textAlign: "left",
        px: 1,
        py: 1,
        "&:hover": {
          bgcolor: (t) =>
            alpha(
              t.palette.text.primary,
              t.palette.mode === "dark" ? 0.03 : 0.02,
            ),
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography
          noWrap
          sx={{
            flex: 1,
            minWidth: 0,
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            fontFamily: MONO,
            color: hasRoot ? "text.primary" : "text.subtitle",
          }}
        >
          {hasRoot ? trace.name : CALL_TRACES_COPY.noRoot}
        </Typography>
        {trace.status && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              textTransform: "uppercase",
            }}
          >
            {trace.status}
          </Typography>
        )}
      </Stack>
      <Stack
        direction="row"
        spacing={1.5}
        sx={{
          mt: 0.25,
          typography: "s3",
          color: "text.subtitle",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {trace.startTime && <span>{fDateTime(trace.startTime)}</span>}
        {trace.latencyMs != null && (
          <span>{formatLatency(trace.latencyMs)}</span>
        )}
        {!hasRoot && (
          <Box component="span" sx={{ fontFamily: MONO }}>
            {trace.traceId}
          </Box>
        )}
      </Stack>
    </ButtonBase>
  );
}
TraceRow.propTypes = {
  trace: PropTypes.shape({
    traceId: PropTypes.string.isRequired,
    name: PropTypes.string,
    startTime: PropTypes.string,
    status: PropTypes.string,
    latencyMs: PropTypes.number,
  }).isRequired,
  onOpen: PropTypes.func.isRequired,
};
