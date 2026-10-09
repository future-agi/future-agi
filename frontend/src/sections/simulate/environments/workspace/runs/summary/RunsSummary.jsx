import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box, Stack, Typography, Button, TextField, MenuItem, Checkbox, ListItemText, CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { RUNS_PAGE_SIZE } from "src/api/simulate-environments/runs";
import SectionCard from "../../../components/SectionCard";
import { useRunsSummary } from "./useRunsSummary";
import SummaryGraph from "./SummaryGraph";
import SummaryLegend from "./SummaryLegend";
import SummaryTable from "./SummaryTable";
import { countCoveredScenarios } from "./summaryData";

// The Runs tab: every run of the environment as one summary — the eval-score
// trend graph over the runs table. Choosing a winner is a later phase,
// surfaced as "coming soon" so the shell matches the design without faking the
// behaviour.
// Title, legend, graph and the runs bar (~440px) plus ~320px of table rows.
const MIN_SUMMARY_PX = 760;
// How many eval lines the graph draws before the user picks their own.
const DEFAULT_SHOWN_EVALS = 5;
const GRAPH_RUN_OPTIONS = [1, 2, 3, 4, 5];
const DEFAULT_GRAPH_RUNS = 1;

export default function RunsSummary({ env, envState, onOpenRun, onGo }) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(RUNS_PAGE_SIZE);
  const [graphRuns, setGraphRuns] = useState(DEFAULT_GRAPH_RUNS);
  const {
    rows,
    rowsChrono,
    evals,
    series,
    count,
    coveredScenarioCount,
    isLoading,
  } = useRunsSummary(env, envState, { page, pageSize }, graphRuns);
  // The server counts over every run of the environment; the page-local
  // fallback only serves mock runs and environments with no run-test.
  const scenarioCount =
    coveredScenarioCount ??
    countCoveredScenarios(rows, envState.scenarios?.length ?? 0);

  // Which eval lines to draw. Until the user picks, the first five; the last
  // one cannot be unticked (an empty chart reads as a bug, not a choice).
  const [pickedIds, setPickedIds] = useState(null);
  const [highlightedId, setHighlightedId] = useState(null);
  const shown = pickedIds
    ? evals.filter((e) => pickedIds.includes(e.id))
    : evals.slice(0, DEFAULT_SHOWN_EVALS);
  const shownSeries = series.filter((s) => shown.some((e) => e.id === s.id));
  const categories = rowsChrono.map((r, i) =>
    i === rowsChrono.length - 1 ? `${r.label} · latest` : r.label,
  );

  const toggleEval = (ids) => {
    // ids = the currently-checked set from the multi-select.
    if (!ids.length) return; // keep at least one line
    setPickedIds(ids);
  };

  // A deep link to ?tab=runs keeps the tab while the runs load; show that
  // they are loading rather than an empty summary.
  if (isLoading && rows.length === 0) {
    return (
      <Stack alignItems="center" sx={{ py: 6 }}>
        <CircularProgress size={20} />
      </Stack>
    );
  }

  return (
    // The tab's height: the header, legend and graph keep their size and the
    // runs table takes the rest, scrolling on its own, so the graph stays in
    // view however many runs there are. On a short panel it stops shrinking at
    // room for the graph plus a few table rows, and the panel scrolls instead.
    <Box
      sx={{
        p: 2,
        height: `max(100%, ${MIN_SUMMARY_PX}px)`,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 3, flexShrink: 0 }}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            Simulations summary
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {count} {count === 1 ? "run" : "runs"} · {scenarioCount}{" "}
            {scenarioCount === 1 ? "scenario" : "scenarios"}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          <Button
            variant="outlined" size="small" onClick={() => onGo?.("evals")}
            startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            Add Evals
          </Button>
          <CustomTooltip show arrow size="small" title="Choosing a winner is coming soon">
            <span>
              <Button
                variant="outlined" size="small" disabled
                startIcon={<Iconify icon="solar:cup-star-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                Choose winner
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      </Stack>

      {/* The graph's tooltip grows down past the chart; a clipping card
          would cut it off. */}
      <SectionCard
        sx={{ overflow: "visible", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
      >
        {/* eval filter + legend */}
        <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, pt: 1.5, pb: 0.5, flexShrink: 0 }}>
          <TextField
            select size="small"
            value={graphRuns}
            onChange={(e) => setGraphRuns(Number(e.target.value))}
            inputProps={{ "aria-label": "Runs in graph" }}
            sx={{ width: 150, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2", py: 0.5 } }}
          >
            {GRAPH_RUN_OPTIONS.map((n) => (
              <MenuItem key={n} value={n} sx={{ typography: "s2", py: 0.5 }}>
                Latest {n} {n === 1 ? "run" : "runs"}
              </MenuItem>
            ))}
          </TextField>
          {evals.length > 0 && (
            <TextField
              select size="small"
              value={shown.map((e) => e.id)}
              onChange={(e) => toggleEval(e.target.value)}
              SelectProps={{
                multiple: true,
                renderValue: (ids) =>
                  ids.length === evals.length
                    ? `All ${evals.length} evals`
                    : `${ids.length} of ${evals.length} evals`,
              }}
              sx={{ width: 200, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2", py: 0.5 } }}
            >
              {evals.map((e) => {
                const on = shown.some((x) => x.id === e.id);
                return (
                  <MenuItem key={e.id} value={e.id} sx={{ typography: "s2", py: 0.5 }}>
                    <Checkbox size="small" checked={on} disabled={on && shown.length === 1} sx={{ p: 0.5, mr: 0.75 }} />
                    <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: e.color, mr: 1, flexShrink: 0 }} />
                    <ListItemText primaryTypographyProps={{ typography: "s2" }} primary={e.name} />
                  </MenuItem>
                );
              })}
            </TextField>
          )}
          <SummaryLegend evals={shown} onHighlight={setHighlightedId} />
        </Stack>

        <SummaryGraph categories={categories} series={shownSeries} highlightedId={highlightedId} />

        <Box sx={{ px: 2.5, py: 1.25, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
            Runs ({count})
          </Typography>
        </Box>

        <SummaryTable rows={rows} evals={evals} onOpenRun={onOpenRun} />
        {count > RUNS_PAGE_SIZE && (
          <Box sx={{ flexShrink: 0, borderTop: "1px solid", borderColor: "divider" }}>
            <DataTablePagination
              page={page}
              pageSize={pageSize}
              total={count}
              onPageChange={setPage}
              onPageSizeChange={(n) => {
                setPageSize(n);
                setPage(0);
              }}
            />
          </Box>
        )}
      </SectionCard>
    </Box>
  );
}

RunsSummary.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }).isRequired,
  envState: PropTypes.shape({ scenarios: PropTypes.array }).isRequired,
  onOpenRun: PropTypes.func,
  onGo: PropTypes.func,
};
