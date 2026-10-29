import PropTypes from "prop-types";
import { useQueries } from "@tanstack/react-query";
import {
  Box,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { formatMetricTime, formatMetricWindow } from "./metricTime";
import {
  ResultIdentityError,
  verifyResultIdentity,
} from "./useCommittedMetricResults";

/** History values come from each row's own verified overall commit, never drafts. */
export default function MetricRunHistory({
  runs,
  selectedRun,
  metricId,
  scope,
  onSelect,
  onClear,
}) {
  const rows = runs.map((item) =>
    item.id === selectedRun?.id ? selectedRun : item,
  );
  const values = useQueries({
    queries: rows.map((item) => ({
      queryKey: [
        "metric-history-value",
        scope,
        metricId,
        item.id,
        item.result_id,
      ],
      enabled: item.metric_id === metricId && Boolean(item.result_id),
      retry: false,
      staleTime: 30000,
      queryFn: async ({ signal }) => {
        const read = async (run) => ({
          run,
          page: verifyResultIdentity(
            (
              await axios.get(endpoints.derivedMetrics.results(run.id), {
                signal,
                params: {
                  rollup: "overall",
                  limit: 1,
                  include_uncertainty: false,
                },
              })
            ).data,
            run.result_id,
          ),
        });
        try {
          return await read(item);
        } catch (error) {
          if (!(error instanceof ResultIdentityError) || !error.mismatch)
            throw error;
          // A history row can race a recompute independently of the selected
          // run. Reconcile once per attempt (including Retry value); a second
          // mismatch stops here. Cache only the verified run/page pair.
          const refreshed = (
            await axios.get(endpoints.derivedMetrics.run(item.id), { signal })
          ).data;
          if (refreshed.id !== item.id || refreshed.metric_id !== metricId)
            throw new ResultIdentityError(false);
          return read(refreshed);
        }
      },
    })),
  });
  return (
    <Box sx={{ minWidth: 0 }}>
      {onClear && (
        <Button size="small" onClick={onClear}>
          Clear selected run
        </Button>
      )}
      <TableContainer>
        <Table
          size="small"
          aria-label="Past metric runs"
          sx={{ minWidth: 760 }}
        >
          <TableHead>
            <TableRow>
              {["Window (UTC)", "Status", "Value", "Version", "Created"].map(
                (label) => (
                  <TableCell key={label}>{label}</TableCell>
                ),
              )}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((listedRun, index) => {
              const query = values[index];
              // Keep live status updates for the same commit; use refreshed
              // metadata only when reconciliation found a different commit.
              const item =
                query.data && query.data.run.result_id !== listedRun.result_id
                  ? query.data.run
                  : listedRun;
              const result = query.data?.page.results.find(
                (entry) => entry.group_key === "",
              );
              const identityValid = item.metric_id === metricId;
              return (
                <TableRow
                  key={item.id}
                  hover
                  selected={item.id === selectedRun?.id}
                  onClick={() => identityValid && onSelect(item.id)}
                  sx={{
                    height: 44,
                    cursor: identityValid ? "pointer" : "default",
                  }}
                >
                  <TableCell>
                    <Button
                      size="small"
                      disabled={!identityValid}
                      title={`${item.window_start} to ${item.window_end}`}
                      sx={{
                        p: 0,
                        height: "auto",
                        textAlign: "left",
                        justifyContent: "flex-start",
                      }}
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(item.id);
                      }}
                    >
                      {formatMetricWindow(item)}
                    </Button>
                  </TableCell>
                  <TableCell>
                    {identityValid ? item.status : "Identity unavailable"}
                  </TableCell>
                  <TableCell>
                    {!identityValid ? (
                      "Unavailable"
                    ) : !item.result_id ? (
                      "No committed value"
                    ) : query.isError ? (
                      <Button
                        size="small"
                        onClick={(event) => {
                          event.stopPropagation();
                          query.refetch();
                        }}
                      >
                        Retry value
                      </Button>
                    ) : !result ? (
                      query.isPending ? (
                        "Loading…"
                      ) : (
                        "Unavailable"
                      )
                    ) : result.value == null ? (
                      "No value"
                    ) : (
                      new Intl.NumberFormat(undefined, {
                        maximumSignificantDigits: 6,
                      }).format(result.value)
                    )}
                  </TableCell>
                  <TableCell>v{item.metric_version}</TableCell>
                  <TableCell>
                    {item.created_at
                      ? formatMetricTime(item.created_at)
                      : "Time unavailable"}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
      {rows.length === 0 && (
        <Typography variant="body2" sx={{ p: 2 }}>
          No saved runs yet.
        </Typography>
      )}
    </Box>
  );
}
MetricRunHistory.propTypes = {
  runs: PropTypes.array.isRequired,
  selectedRun: PropTypes.object,
  metricId: PropTypes.string.isRequired,
  scope: PropTypes.string.isRequired,
  onSelect: PropTypes.func.isRequired,
  onClear: PropTypes.func,
};
