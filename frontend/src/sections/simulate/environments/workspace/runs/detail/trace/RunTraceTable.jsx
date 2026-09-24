import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Stack, Button, Pagination } from "@mui/material";

import Iconify from "src/components/iconify";
import { FilterPanel } from "src/components/filter-panel";
import { useRunCalls } from "src/api/simulate-environments/runDetail";

import SectionCard from "../../../../components/SectionCard";
import EmptyState from "../../../../components/EmptyState";
import TraceTable from "./TraceTable";
import { TraceGroupByPicker, TraceColumnsPicker } from "./TracePickers";
import StatusFilterChips from "./StatusFilterChips";
import { defaultTraceColumns } from "./traceTable.constants";

const GROUP_BY_API = { useCase: "goal", status: "status" };
const STATUS_CHIP_API = {
  failing: "failed",
  errored: "error",
  inconclusive: "inconclusive",
  passing: "passed",
};
const STATUS_LABELS = {
  passed: "Passed",
  failed: "Failed",
  error: "Errored",
  inconclusive: "Not measured",
};

const filterButtonSx = {
  typography: "s2",
  fontWeight: "fontWeightBold",
  textTransform: "none",
  height: 32,
  color: "text.primary",
  borderColor: "divider",
  "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
};

// The per-call table owns server-backed grouping, filtering, columns and paging
// for read-only execution results.
export default function RunTraceTable({
  executionId,
  onOpenCall,
  initialFilters = {},
}) {
  const [groupBy, setGroupBy] = useState("useCase");
  const [statusChip, setStatusChip] = useState("all");
  const [page, setPage] = useState(1);
  const [visibleColumns, setVisibleColumns] = useState(() =>
    defaultTraceColumns(),
  );
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [filters, setFilters] = useState(initialFilters);

  const serverFilters = useMemo(() => {
    const next = {};
    if (filters.goal?.length) next.goal = filters.goal;
    if (filters.subGoal?.length) next.sub_goal = filters.subGoal;
    if (filters.status?.length) next.status = filters.status;
    if (statusChip !== "all") next.status = [STATUS_CHIP_API[statusChip]];
    return next;
  }, [filters, statusChip]);

  const {
    tasks,
    columns,
    count = 0,
    groups = [],
    facets = {},
    totalPages = 1,
    isLoading,
    error,
  } = useRunCalls(executionId, {
    page,
    limit: 50,
    filters: serverFilters,
    groupBy: GROUP_BY_API[groupBy],
  });

  // The eval columns to render come from the data-driven column descriptors.
  const evals = useMemo(
    () =>
      columns
        .filter((c) => c.group === "Evaluations")
        .map((c) => ({ id: c.key, name: c.label })),
    [columns],
  );

  const goalOptions = useMemo(
    () => (facets.goal ?? []).map((item) => item.value),
    [facets.goal],
  );
  const subGoalOptions = useMemo(
    () => (facets.sub_goal ?? []).map((item) => item.value),
    [facets.sub_goal],
  );
  const filterFields = useMemo(
    () => [
      {
        value: "goal",
        label: "Goal",
        type: "enum",
        choices: goalOptions,
      },
      {
        value: "subGoal",
        label: "Sub goal",
        type: "enum",
        choices: subGoalOptions,
      },
      {
        value: "status",
        label: "Status",
        type: "enum",
        choices: Object.keys(STATUS_LABELS),
        choiceLabels: STATUS_LABELS,
      },
    ],
    [goalOptions, subGoalOptions],
  );
  const filterCount = Object.values(filters).reduce(
    (a, v) => a + (v?.length || 0),
    0,
  );

  const statusCounts = useMemo(() => {
    const byStatus = Object.fromEntries(
      (facets.status ?? []).map((item) => [item.value, item.count]),
    );
    return {
      all: Object.values(byStatus).reduce((sum, count) => sum + count, 0),
      failing: byStatus.failed ?? 0,
      errored: byStatus.error ?? 0,
      mixed: 0,
      inconclusive: byStatus.inconclusive ?? 0,
      passing: byStatus.passed ?? 0,
    };
  }, [facets.status]);

  const applyFilters = (result) => {
    if (!result) {
      setFilters({});
      return;
    }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value)
          ? r.value
          : r.value != null
            ? [r.value]
            : [];
        if (values.length)
          flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      setFilters(result);
    }
    setStatusChip("all");
  };

  const title = (
    <Stack direction="row" alignItems="center" spacing={1.25}>
      <TraceGroupByPicker
        value={groupBy}
        onChange={(value) => {
          setGroupBy(value);
          setPage(1);
        }}
      />
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setFilterAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon="mage:filter"
            width={15}
            sx={{ color: filterCount ? "primary.main" : "text.subtitle" }}
          />
        }
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.subtitle" }}
          />
        }
        sx={filterButtonSx}
      >
        Filter
        {filterCount > 0 && (
          <>
            <Box
              component="span"
              sx={{
                mx: 0.5,
                color: "text.subtitle",
                fontWeight: "fontWeightRegular",
              }}
            >
              ·
            </Box>
            <Box component="span" sx={{ color: "primary.main" }}>
              {filterCount}
            </Box>
          </>
        )}
      </Button>
    </Stack>
  );

  const action = (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <StatusFilterChips
        value={statusChip}
        counts={statusCounts}
        onChange={(value) => {
          setStatusChip(value);
          setFilters((current) => {
            if (!current.status) return current;
            const { status: _status, ...rest } = current;
            return rest;
          });
          setPage(1);
        }}
      />
      <TraceColumnsPicker value={visibleColumns} onChange={setVisibleColumns} />
    </Stack>
  );
  return (
    <>
      <SectionCard title={title} action={action}>
        {isLoading ? (
          <EmptyState icon="solar:hourglass-linear" title="Loading calls…" />
        ) : error ? (
          <EmptyState
            icon="solar:danger-triangle-linear"
            title="Couldn't load calls"
            body="Something went wrong loading this run's calls. Try again."
          />
        ) : tasks.length === 0 ? (
          <EmptyState
            icon="solar:filter-linear"
            title="No calls match that filter"
          />
        ) : (
          <TraceTable
            columns={visibleColumns}
            groups={groups}
            evals={evals}
            onOpen={onOpenCall}
          />
        )}
        {!isLoading && count > 0 && totalPages > 1 && (
          <Stack direction="row" justifyContent="center" sx={{ py: 2 }}>
            <Pagination
              count={totalPages}
              page={page}
              onChange={(_, value) => setPage(value)}
              size="small"
            />
          </Stack>
        )}
      </SectionCard>

      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={(result) => {
          applyFilters(result);
          setPage(1);
        }}
        aiPlaceholder="Ask AI — e.g. 'show calls that failed the refund eval'"
        placement="bottom-start"
      />
    </>
  );
}
RunTraceTable.propTypes = {
  executionId: PropTypes.string,
  onOpenCall: PropTypes.func,
  initialFilters: PropTypes.object,
};
