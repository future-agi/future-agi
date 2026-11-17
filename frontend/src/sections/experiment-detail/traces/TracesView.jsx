// W5-8 Traces view (RV-14; W5-SCOPE §7 C6): one run's traces, one per try,
// with the summary line `30 traces · 4 errors · p50 2.1 s · $0.09`, filters
// Status and Failed eval, and Open (the trace drawer).
import React, { useState } from "react";
import PropTypes from "prop-types";
import { keepPreviousData } from "@tanstack/react-query";
import {
  Box,
  Button,
  Chip,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useExperimentRunTraces } from "src/api/develop/experiment-run-traces";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import {
  TRACE_STATUSES,
  fmtCost,
  fmtDuration,
  runTracesView,
  summaryLine,
} from "./tracesModel";

const STATUS_COLOR = { ok: "success", error: "error", infra: "warning" };
// The route's largest page (GET experiment-runs/<run>/traces/, limit 1-100).
const PAGE = 100;

// The run's traces from GET /model-hub/experiment-runs/{run_id}/traces/
// (the backend route; it takes only limit and offset), read as the view's
// rows. Status filters the page in the browser.
function useRunTraces(runId, { status, offset }) {
  return useExperimentRunTraces(
    runId,
    { limit: PAGE, offset },
    {
      select: (res) => runTracesView(res?.data?.result, { status }),
      placeholderData: keepPreviousData,
    },
  );
}

export default function TracesView({ runId }) {
  const [status, setStatus] = useState("");
  const [failedEval, setFailedEval] = useState("");
  const [openTrace, setOpenTrace] = useState(null);
  // The page offset belongs to one run: another run starts at its first page.
  const [paging, setPaging] = useState({ runId, offset: 0 });
  const offset = paging.runId === runId ? paging.offset : 0;
  const { data, isLoading, isError, refetch } = useRunTraces(runId, {
    status,
    offset,
  });
  const filtered = Boolean(status || failedEval);
  const paged = Boolean(data && (data.offset > 0 || data.has_more));

  return (
    <Box
      data-testid="run-traces"
      sx={{ display: "flex", flexDirection: "column", gap: 1.5, minHeight: 0 }}
    >
      <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
        <Typography variant="subtitle2" data-testid="run-traces-summary">
          {data
            ? summaryLine(data.summary)
            : isLoading
              ? "Loading traces…"
              : ""}
        </Typography>
        {filtered && data ? (
          <Typography
            variant="caption"
            color="text.secondary"
            data-testid="run-traces-filtered"
          >
            Showing {data.count.toLocaleString()} of{" "}
            {data.summary.traces.toLocaleString()}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1 }} />
        <TextField
          select
          size="small"
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          sx={{ minWidth: 140 }}
          SelectProps={{ displayEmpty: true }}
          InputLabelProps={{ shrink: true }}
        >
          {TRACE_STATUSES.map((s) => (
            <MenuItem key={s.value || "any"} value={s.value}>
              {s.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Failed eval"
          value={failedEval}
          onChange={(e) => setFailedEval(e.target.value)}
          sx={{ minWidth: 200 }}
          disabled={!data?.failed_evals?.length}
          SelectProps={{ displayEmpty: true }}
          InputLabelProps={{ shrink: true }}
        >
          <MenuItem value="">Any eval</MenuItem>
          {(data?.failed_evals ?? []).map((f) => (
            <MenuItem key={f.key} value={f.key}>
              {f.name} · {f.count.toLocaleString()}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
      {isError ? (
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography color="error.main" variant="body2">
            Couldn’t load this run’s traces. Retry, or reload the page.
          </Typography>
          <Button size="small" onClick={() => refetch?.()}>
            Retry
          </Button>
        </Stack>
      ) : null}
      <Box
        sx={{
          overflow: "auto",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Table
          size="small"
          stickyHeader
          // Headers and the row label never wrap (`Row 10`, `First error`).
          sx={{
            "& th": { whiteSpace: "nowrap" },
            "& td:first-of-type": { whiteSpace: "nowrap" },
          }}
        >
          <TableHead>
            <TableRow>
              <TableCell>Row</TableCell>
              <TableCell>Try</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right">Duration</TableCell>
              <TableCell align="right">Tokens</TableCell>
              <TableCell align="right">Cost</TableCell>
              <TableCell>Failed evals</TableCell>
              <TableCell>First error</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {(data?.results ?? []).map((t) => (
              <TableRow
                key={`${t.trace_id}-${t.example_id ?? ""}-${t.trial ?? ""}`}
                hover
                data-testid="run-trace-row"
                data-trace-id={t.trace_id}
              >
                <TableCell>{t.row_label}</TableCell>
                <TableCell>{t.trial}</TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    variant="outlined"
                    color={STATUS_COLOR[t.status] ?? "default"}
                    label={t.status}
                  />
                </TableCell>
                <TableCell align="right">
                  {fmtDuration(t.duration_ms)}
                </TableCell>
                <TableCell align="right">
                  {t.tokens != null ? t.tokens.toLocaleString() : "–"}
                </TableCell>
                <TableCell align="right">{fmtCost(t.cost_usd)}</TableCell>
                <TableCell>
                  {t.failed_evals.length
                    ? t.failed_evals
                        .map(
                          (k) =>
                            data.failed_evals.find((f) => f.key === k)?.name ??
                            k,
                        )
                        .join(", ")
                    : "–"}
                </TableCell>
                <TableCell
                  sx={{
                    maxWidth: 260,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={t.first_error ?? ""}
                >
                  {t.first_error ?? "–"}
                </TableCell>
                <TableCell align="right">
                  <Button size="small" onClick={() => setOpenTrace(t)}>
                    Open
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {data && !data.results.length ? (
              <TableRow>
                <TableCell colSpan={9}>
                  <Typography variant="body2" color="text.secondary">
                    {filtered
                      ? "No traces match these filters."
                      : "This run has no traces yet."}
                  </Typography>
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </Box>
      {paged ? (
        <Stack direction="row" gap={1}>
          <Button
            size="small"
            disabled={!(data.offset > 0)}
            onClick={() =>
              setPaging({ runId, offset: Math.max(0, offset - PAGE) })
            }
          >
            Previous
          </Button>
          <Button
            size="small"
            disabled={!data.has_more}
            onClick={() => setPaging({ runId, offset: offset + PAGE })}
          >
            Next
          </Button>
        </Stack>
      ) : null}
      {openTrace ? (
        <TraceDetailDrawerV2
          traceId={openTrace.trace_id}
          projectId={openTrace.project_id ?? undefined}
          open={Boolean(openTrace)}
          onClose={() => setOpenTrace(null)}
        />
      ) : null}
    </Box>
  );
}

TracesView.propTypes = {
  experimentId: PropTypes.string,
  runId: PropTypes.string,
};
