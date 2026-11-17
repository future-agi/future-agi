import {
  Box,
  Button,
  Chip,
  Link,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  notJoinedReasonText,
  runTitle,
  useExperimentRunHeader,
  useExperimentRunTraces,
} from "src/api/develop/experiment-run-traces";

const PAGE = 50;

const shortId = (value) =>
  value && value.length > 16
    ? `${value.slice(0, 8)}…${value.slice(-6)}`
    : value;

const traceHref = (row) =>
  row.project_id
    ? `/dashboard/observe/${row.project_id}/trace/${row.trace_id}`
    : null;

// The run named by the page's run= parameter (the run's app_url opens this
// tab with it) and that run's traces, joined by run id: only traces whose root
// span carries this run's fi.experiment.run_id are listed.
const ExperimentRunTracesPanel = ({ runId }) => {
  const theme = useTheme();
  // The page offset belongs to one run: moving to another run's link starts
  // that run at its first page (the call site also keys the panel by run id).
  const [paging, setPaging] = useState({ runId, offset: 0 });
  const offset = paging.runId === runId ? paging.offset : 0;
  const setOffset = (next) => setPaging({ runId, offset: next });
  const header = useExperimentRunHeader(runId);
  const traces = useExperimentRunTraces(runId, { limit: PAGE, offset });
  const run = header.data;
  const page = traces.data;
  const failed = header.isError || traces.isError;

  return (
    <Box
      data-testid="experiment-run-traces-panel"
      data-run-id={runId}
      sx={{
        mx: theme.spacing(1.5),
        my: theme.spacing(1),
        p: theme.spacing(1.5),
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: theme.spacing(1),
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
        <Typography variant="s1" fontWeight="fontWeightSemiBold">
          {header.isLoading ? (
            <Skeleton width={160} />
          ) : (
            `SDK run ${runTitle(run) || runId}`
          )}
        </Typography>
        {run?.status ? (
          <Chip size="small" label={run.status} variant="outlined" />
        ) : null}
        <Typography
          variant="s2"
          color="text.secondary"
          data-testid="experiment-run-traces-count"
        >
          {page ? `${page.items_with_trace} items with a trace` : ""}
        </Typography>
      </Box>

      {failed ? (
        <Typography
          variant="s2"
          color="error.main"
          data-testid="experiment-run-traces-error"
        >
          This run or its traces could not be loaded. It may be in another
          workspace, or the trace store may be unavailable; reload to retry.
        </Typography>
      ) : null}

      {traces.isLoading ? <Skeleton height={64} /> : null}

      {page && !failed ? (
        <>
          {page.traces.length === 0 ? (
            <Typography variant="s2" color="text.secondary">
              No trace of this run is in the trace store yet.
            </Typography>
          ) : (
            <Table size="small" data-testid="experiment-run-traces-table">
              <TableHead>
                <TableRow>
                  <TableCell>Trace</TableCell>
                  <TableCell>Example</TableCell>
                  <TableCell>Trial</TableCell>
                  <TableCell>Root span</TableCell>
                  <TableCell>Spans</TableCell>
                  <TableCell>Item</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {page.traces.map((row) => {
                  const href = traceHref(row);
                  return (
                    <TableRow
                      key={`${row.trace_id}-${row.example_id}-${row.trial_number}`}
                      data-testid="experiment-run-trace-row"
                      data-trace-id={row.trace_id}
                      data-trace-run-id={row.trace_run_id}
                    >
                      <TableCell title={row.trace_id}>
                        {href ? (
                          <Link component={RouterLink} to={href}>
                            {shortId(row.trace_id)}
                          </Link>
                        ) : (
                          shortId(row.trace_id)
                        )}
                      </TableCell>
                      <TableCell title={row.example_id}>
                        {shortId(row.example_id)}
                      </TableCell>
                      <TableCell>{row.trial_number}</TableCell>
                      <TableCell>{row.root_span_name ?? "-"}</TableCell>
                      <TableCell>{row.span_count}</TableCell>
                      <TableCell>{row.item_status}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
          {page.not_joined.length > 0 ? (
            <Box sx={{ mt: 1 }} data-testid="experiment-run-traces-not-joined">
              <Typography variant="s2" color="text.secondary">
                {`${page.not_joined.length} recorded trace${
                  page.not_joined.length === 1 ? " is" : "s are"
                } not listed:`}
              </Typography>
              {page.not_joined.map((row) => (
                <Typography
                  key={`nj-${row.trace_id}-${row.example_id}-${row.trial_number}`}
                  variant="s3"
                  color="text.secondary"
                  component="div"
                  data-testid="experiment-run-trace-not-joined"
                  data-trace-id={row.trace_id}
                  data-reason={row.reason}
                >
                  {`${shortId(row.trace_id)}: ${
                    notJoinedReasonText[row.reason] ?? row.reason
                  }`}
                </Typography>
              ))}
            </Box>
          ) : null}
          {page.offset > 0 || page.has_more ? (
            <Box sx={{ display: "flex", gap: 1, mt: 1 }}>
              <Button
                size="small"
                disabled={page.offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                Previous
              </Button>
              <Button
                size="small"
                disabled={!page.has_more}
                onClick={() => setOffset(offset + PAGE)}
              >
                Next
              </Button>
            </Box>
          ) : null}
        </>
      ) : null}
    </Box>
  );
};

ExperimentRunTracesPanel.propTypes = {
  runId: PropTypes.string.isRequired,
};

export default ExperimentRunTracesPanel;
