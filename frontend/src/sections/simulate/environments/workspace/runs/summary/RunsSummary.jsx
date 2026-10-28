import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { RUNS_PAGE_SIZE } from "src/api/simulate-environments/runs";
import { ALL_RUNS_CAP } from "src/api/simulate-environments/useAllRuns";
import SectionCard from "../../../components/SectionCard";
import { useRunsSummary } from "./useRunsSummary";
import { useRunsOverview } from "./useRunsOverview";
import RunsHeadline from "./RunsHeadline";
import EvalCheckChips from "./EvalCheckChips";
import PassRateTrend from "./PassRateTrend";
import SummaryTable from "./SummaryTable";
import { countCoveredScenarios } from "./summaryData";

// The Runs tab: the latest pass rate and the pass rate across every run, with
// the evals as chips that draw themselves on the line, over the runs table.
// Choosing a winner is a later phase, surfaced as "coming soon" so the shell
// matches the design without faking the behaviour.
// The overview card and the runs bar (~380px) plus ~320px of table rows.
const MIN_SUMMARY_PX = 760;

export default function RunsSummary({ env, envState, onOpenRun, onGo }) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(RUNS_PAGE_SIZE);
  const [focusId, setFocusId] = useState(null);
  const { rows, evals, count, coveredScenarioCount, isLoading } =
    useRunsSummary(env, envState, { page, pageSize });
  const overview = useRunsOverview(env, envState, focusId, rows);
  const focusEval = overview.evals.find((e) => e.id === focusId) || null;
  // The server counts over every run of the environment; the page-local
  // fallback only serves mock runs and environments with no run-test.
  const scenarioCount =
    coveredScenarioCount ??
    countCoveredScenarios(rows, envState.scenarios?.length ?? 0);

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
    // The tab's height: the overview keeps its size and the runs table takes
    // the rest, scrolling on its own, so the chart stays in view however many
    // runs there are. On a short panel it stops shrinking at room for the
    // chart plus a few table rows, and the panel scrolls instead.
    <Box
      sx={{
        p: 2,
        height: `max(100%, ${MIN_SUMMARY_PX}px)`,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <SectionCard sx={{ mb: 2, flexShrink: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          useFlexGap
          spacing={2}
          sx={{ px: 2.5, py: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          <RunsHeadline latest={overview.latest} previous={overview.previous} />
          {overview.evals.length > 0 && (
            <EvalCheckChips
              evals={overview.evals}
              latest={overview.latest}
              previous={overview.previous}
              focusId={focusId}
              onFocus={setFocusId}
            />
          )}
        </Stack>
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <PassRateTrend
            rows={overview.rows}
            focusEval={focusEval}
            capped={overview.capped}
            cap={ALL_RUNS_CAP}
            isLoading={overview.isLoading}
            onOpenRun={onOpenRun}
          />
        </Box>
      </SectionCard>

      <SectionCard
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2.5, py: 1, flexShrink: 0 }}
        >
          <Typography
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            Runs ({count})
          </Typography>
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            across {scenarioCount}{" "}
            {scenarioCount === 1 ? "scenario" : "scenarios"}
          </Typography>
          <Button
            variant="outlined"
            size="small"
            onClick={() => onGo?.("evals")}
            startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
            sx={{
              typography: "s2",
              fontWeight: "fontWeightBold",
              flexShrink: 0,
            }}
          >
            Add Evals
          </Button>
          <CustomTooltip
            show
            arrow
            size="small"
            title="Choosing a winner is coming soon"
          >
            <span>
              <Button
                variant="outlined"
                size="small"
                disabled
                startIcon={<Iconify icon="solar:cup-star-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                Choose winner
              </Button>
            </span>
          </CustomTooltip>
        </Stack>

        <SummaryTable rows={rows} evals={evals} onOpenRun={onOpenRun} />
        {count > RUNS_PAGE_SIZE && (
          <Box
            sx={{
              flexShrink: 0,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
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
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })
    .isRequired,
  envState: PropTypes.shape({ scenarios: PropTypes.array }).isRequired,
  onOpenRun: PropTypes.func,
  onGo: PropTypes.func,
};
