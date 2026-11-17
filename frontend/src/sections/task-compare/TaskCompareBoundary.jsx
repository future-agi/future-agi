// IT-R3 G4 P1 · a local error boundary for Side by side. A render error in
// one task (a lane, a step, a graph) keeps the app shell, says what broke
// and offers the two ways out: the raw trace of that task and the list the
// user came from. It resets on route, runs and row changes, so Back, the
// rail and the lane picker always recover (the app-level boundary used to
// replace the whole page and keep it after Back).
import React, {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import { ErrorBoundary } from "react-error-boundary";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { logger } from "src/utils/logger";
import { matrixHref } from "src/sections/common/Analysis/analysisLinks";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";

const ReportCtx = createContext(null);

/**
 * The workspace writes what it last knew (the task's trace id) during
 * render, so the fallback can still open the raw trace after a child threw.
 * @returns {{current: {traceId?: string|null}}|null}
 */
export function useBoundaryReport() {
  return useContext(ReportCtx);
}

/** `from` (a path) beats the Matrix of the runs in the URL. */
export function boundaryBackHref(datasetId, search) {
  const sp = new URLSearchParams(search ?? "");
  const from = sp.get("from");
  if (from && from.startsWith("/")) return from;
  const runIds = (sp.get("runs") ?? "").split(",").filter(Boolean);
  return matrixHref({
    mode: "compare",
    datasetId,
    runIds,
    baselineId: sp.get("baseline") ?? undefined,
    row: sp.get("row") ?? undefined,
  });
}

const firstLine = (error) =>
  String(error?.message ?? error ?? "Unknown error")
    .split("\n")[0]
    .slice(0, 220);

function Fallback({ error, resetErrorBoundary, datasetId, report }) {
  const navigate = useNavigate();
  const { search } = useLocation();
  const [traceOpen, setTraceOpen] = useState(false);
  const traceId = report?.current?.traceId ?? null;
  const back = () => {
    resetErrorBoundary();
    navigate(boundaryBackHref(datasetId, search));
  };
  return (
    <Box
      role="alert"
      data-testid="tc-boundary"
      sx={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: 3,
        minHeight: 320,
      }}
    >
      <Stack spacing={1.25} alignItems="center" sx={{ maxWidth: 520 }}>
        <Iconify
          icon="mdi:alert-circle-outline"
          width={28}
          sx={{ color: "error.main" }}
        />
        <Typography variant="subtitle1" fontWeight={600}>
          This task could not render.
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          textAlign="center"
          sx={{ wordBreak: "break-word" }}
        >
          {firstLine(error)}
        </Typography>
        <Stack direction="row" spacing={1}>
          <CustomTooltip
            show={!traceId}
            title="No trace was recorded for this task"
            size="small"
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                disabled={!traceId}
                onClick={() => setTraceOpen(true)}
                startIcon={<Iconify icon="mdi:file-tree-outline" width={16} />}
              >
                Open raw trace
              </Button>
            </span>
          </CustomTooltip>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={back}
            startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
          >
            Back to the list
          </Button>
        </Stack>
      </Stack>
      {traceOpen && traceId ? (
        <TraceDetailDrawerV2
          traceId={traceId}
          open
          onClose={() => setTraceOpen(false)}
        />
      ) : null}
    </Box>
  );
}

Fallback.propTypes = {
  error: PropTypes.any,
  resetErrorBoundary: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  report: PropTypes.object,
};

/**
 * @param {{datasetId: string, children: React.ReactNode}} props
 */
export default function TaskCompareBoundary({ datasetId, children }) {
  const { pathname, search } = useLocation();
  const report = useRef({ traceId: null });
  const resetKeys = useMemo(() => {
    const sp = new URLSearchParams(search);
    return [pathname, sp.get("runs") ?? "", sp.get("row") ?? ""];
  }, [pathname, search]);
  return (
    <ReportCtx.Provider value={report}>
      <ErrorBoundary
        resetKeys={resetKeys}
        onError={(error, info) =>
          logger.error?.("[TaskCompare] render error", {
            error: firstLine(error),
            stack: info?.componentStack?.slice(0, 600),
          })
        }
        fallbackRender={(props) => (
          <Fallback {...props} datasetId={datasetId} report={report} />
        )}
      >
        {children}
      </ErrorBoundary>
    </ReportCtx.Provider>
  );
}

TaskCompareBoundary.propTypes = {
  datasetId: PropTypes.string,
  children: PropTypes.node,
};
