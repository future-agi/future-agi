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
import RunCompare from "./RunCompare";
import { COMPARE_COPY } from "../../workspace.constants";

const RUN_COMPARE_ENABLED = false;

// The Runs tab: every run of the environment as one summary — the eval-score
// trend graph over the runs table. With versioning on, two finished runs can
// be ticked and compared. Choosing a winner is a later phase,
// surfaced as "coming soon" so the shell matches the design without faking the
// behaviour.
// Title, legend, graph and the runs bar (~440px) plus ~320px of table rows.
const MIN_SUMMARY_PX = 760;
// How many eval lines the graph draws before the user picks their own.
const DEFAULT_SHOWN_EVALS = 5;
const GRAPH_RUN_OPTIONS = [1, 2, 3, 4, 5];
const DEFAULT_GRAPH_RUNS = 1;

export default function RunsSummary({
  env,
  envState,
  scenarioCount: scenarioCountProp,
  onOpenRun,
  onGo,
}) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(RUNS_PAGE_SIZE);
  const [graphRuns, setGraphRuns] = useState(DEFAULT_GRAPH_RUNS);
  // The selected rows themselves, so a selection survives paging.
  const [selected, setSelected] = useState([]);
  const [comparing, setComparing] = useState(false);
  // TODO: compare is hidden until its place in the product is decided.
  const canCompare = RUN_COMPARE_ENABLED && !!env?.versioning?.enabled;
  const selectedIds = selected.map((r) => r.executionId);
  const canSelect = (row) =>
    selected.length < 2 && !!row.executionId && row.runState === "finished";
  const toggleSelect = (row) =>
    setSelected((prev) =>
      prev.some((r) => r.executionId === row.executionId)
        ? prev.filter((r) => r.executionId !== row.executionId)
        : [...prev, row],
    );
  const {
    rows,
    rowsChrono,
    evals,
    series,
    count,
    coveredScenarioCount,
    isLoading,
  } = useRunsSummary(env, envState, { page, pageSize }, graphRuns);
  // A ticked run on this page reads its latest state: one no longer finished
  // (re-graded, say) can't be compared.
  const live = selected.map(
    (s) => rows.find((r) => r.executionId === s.executionId) ?? s,
  );
  const comparable =
    live.length === 2 && live.every((r) => r.runState === "finished");
  // The newer run is the one compared; the older is what it is compared with.
  const [current, against] = [...live].sort((a, b) => b.ordinal - a.ordinal);
  // The server counts over every run of the environment; the page-local
  // fallback only serves mock runs and environments with no run-test.
  const scenarioCount =
    coveredScenarioCount ??
    countCoveredScenarios(rows, scenarioCountProp ?? envState.scenarios?.length ?? 0);

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

        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 2.5, py: 1, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
            Runs ({count})
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            across {scenarioCount} {scenarioCount === 1 ? "scenario" : "scenarios"}
          </Typography>
          {canCompare && (
            <>
              <Typography sx={{ typography: "s2", color: "text.secondary", flexShrink: 0 }}>
                {selected.length ? COMPARE_COPY.picked(selected.length) : COMPARE_COPY.pick}
              </Typography>
              {selected.length > 0 && (
                <Button size="small" onClick={() => setSelected([])} sx={{ typography: "s2", flexShrink: 0 }}>
                  {COMPARE_COPY.clear}
                </Button>
              )}
              <Button
                variant="outlined" size="small" disabled={!comparable}
                onClick={() => setComparing(true)}
                sx={{ typography: "s2", fontWeight: "fontWeightBold", flexShrink: 0 }}
              >
                {COMPARE_COPY.compare}
              </Button>
            </>
          )}
          <Button
            variant="outlined" size="small" onClick={() => onGo?.("evals")}
            startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: "fontWeightBold", flexShrink: 0 }}
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

        <SummaryTable
          rows={rows} evals={evals} onOpenRun={onOpenRun}
          {...(canCompare && { selectedIds, canSelect, onToggleSelect: toggleSelect })}
        />
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
      {selected.length === 2 && (
        <RunCompare
          open={comparing} onClose={() => setComparing(false)}
          env={env} current={current} against={against}
        />
      )}
    </Box>
  );
}

RunsSummary.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string, versioning: PropTypes.object }).isRequired,
  envState: PropTypes.shape({ scenarios: PropTypes.array }).isRequired,
  scenarioCount: PropTypes.number,
  onOpenRun: PropTypes.func,
  onGo: PropTypes.func,
};
