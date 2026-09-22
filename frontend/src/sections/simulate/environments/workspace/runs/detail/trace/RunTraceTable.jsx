import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";

import Iconify from "src/components/iconify";
import { FilterPanel } from "src/components/filter-panel";
import { useRunCalls } from "src/api/simulate-environments/runDetail";

import SectionCard from "../../../../components/SectionCard";
import EmptyState from "../../../../components/EmptyState";
import TraceTable from "./TraceTable";
import { TraceGroupByPicker, TraceColumnsPicker } from "./TracePickers";
import StatusFilterChips from "./StatusFilterChips";
import {
  defaultTraceColumns, groupOfTask, statusBucket, statusFilterLabel,
  STATUS_FILTER_LABELS, PATTERN_FILTER_LABELS,
} from "./traceTable.constants";

const filterButtonSx = {
  typography: "s2", fontWeight: "fontWeightBold", textTransform: "none", height: 32,
  color: "text.primary", borderColor: "divider",
  "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
};

// The per-call table for a run's Test-runs tab. Owns the hook, the group-by /
// status / column / filter controls and the row selection, over REAL call data.
export default function RunTraceTable({ executionId, onOpenCall, onFailedCriticalChange, onRerun }) {
  const { tasks, columns, isLoading } = useRunCalls(executionId);

  const [groupBy, setGroupBy] = useState("useCase");
  const [statusChip, setStatusChip] = useState("all");
  const [visibleColumns, setVisibleColumns] = useState(() => defaultTraceColumns());
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [filters, setFilters] = useState({});
  const [selected, setSelected] = useState(() => new Set());

  // The eval columns to render come from the data-driven column descriptors.
  const evals = useMemo(
    () => columns.filter((c) => c.group === "Evaluations").map((c) => ({ id: c.key, name: c.label })),
    [columns],
  );

  // Surface the loaded run's critical-failure count so the run header banner can
  // reflect it. No per-call `critical` feed yet → this reads 0, but the seam is
  // live for when the backend adds the flag.
  const failedCritical = tasks.filter((t) => t.critical && t.status === "failed").length;
  useEffect(() => {
    onFailedCriticalChange?.(failedCritical);
  }, [failedCritical, onFailedCriticalChange]);

  const failureGrouping = groupBy === "subGoal" || groupBy === "pattern";
  const incompatibleChips = failureGrouping ? ["passing", "inconclusive"] : [];
  const setGroupByReconciled = (g) => {
    setGroupBy(g);
    if ((g === "subGoal" || g === "pattern") && (statusChip === "passing" || statusChip === "inconclusive")) {
      setStatusChip("all");
    }
  };

  const scenarioOptions = useMemo(
    () => [...new Set(tasks.map((t) => t.scenario).filter(Boolean))].sort(),
    [tasks],
  );
  const personaOptions = useMemo(
    () => [...new Set(tasks.map((t) => t.persona).filter(Boolean))].sort(),
    [tasks],
  );
  const filterFields = useMemo(() => [
    { value: "scenario", label: "Scenario", type: "enum", choices: scenarioOptions },
    { value: "persona", label: "Persona", type: "enum", choices: personaOptions },
    { value: "status", label: "Status", type: "enum", choices: STATUS_FILTER_LABELS },
    { value: "pattern", label: "Failure pattern", type: "enum", choices: PATTERN_FILTER_LABELS },
  ], [scenarioOptions, personaOptions]);
  const filterCount = Object.values(filters).reduce((a, v) => a + (v?.length || 0), 0);

  const derivations = useMemo(() => {
    const out = new Map();
    tasks.forEach((t) => out.set(t.id, {
      status: statusFilterLabel(t),
      pattern: groupOfTask(t, "pattern"),
    }));
    return out;
  }, [tasks]);

  const shown = useMemo(() => tasks.filter((t) => {
    if (statusChip !== "all" && statusBucket(t) !== statusChip) return false;
    const d = derivations.get(t.id);
    if (filters.scenario?.length && !filters.scenario.includes(t.scenario)) return false;
    if (filters.persona?.length && !filters.persona.includes(t.persona)) return false;
    if (filters.status?.length && !filters.status.includes(d.status)) return false;
    if (filters.pattern?.length && !filters.pattern.includes(d.pattern)) return false;
    return true;
  }), [tasks, statusChip, filters, derivations]);

  const groupableShown = failureGrouping
    ? shown.filter((t) => t.status !== "passed" && t.status !== "unmeasured")
    : shown;

  const statusCounts = useMemo(() => tasks.reduce((c, t) => {
    c.all += 1;
    c[statusBucket(t)] += 1;
    return c;
  }, { all: 0, failing: 0, mixed: 0, inconclusive: 0, passing: 0 }), [tasks]);

  const applyFilters = (result) => {
    if (!result) { setFilters({}); return; }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value) ? r.value : r.value != null ? [r.value] : [];
        if (values.length) flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      setFilters(result);
    }
  };

  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const title = selected.size ? (
    <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>{selected.size} selected</Typography>
  ) : (
    <Stack direction="row" alignItems="center" spacing={1.25}>
      <TraceGroupByPicker value={groupBy} onChange={setGroupByReconciled} />
      <Button
        size="small" variant="outlined"
        onClick={(e) => setFilterAnchor(e.currentTarget)}
        startIcon={<Iconify icon="mage:filter" width={15} sx={{ color: filterCount ? "primary.main" : "text.subtitle" }} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} sx={{ color: "text.subtitle" }} />}
        sx={filterButtonSx}
      >
        Filter
        {filterCount > 0 && (
          <>
            <Box component="span" sx={{ mx: 0.5, color: "text.subtitle", fontWeight: "fontWeightRegular" }}>·</Box>
            <Box component="span" sx={{ color: "primary.main" }}>{filterCount}</Box>
          </>
        )}
      </Button>
    </Stack>
  );

  const action = selected.size ? (
    <Stack direction="row" spacing={1}>
      <Button size="small" onClick={() => setSelected(new Set())} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>
        Clear
      </Button>
      <Button
        variant="contained" color="primary" size="small"
        onClick={() => onRerun?.([...selected])}
        startIcon={<Iconify icon="solar:refresh-bold" width={15} />}
        sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
      >
        Re-run {selected.size}
      </Button>
    </Stack>
  ) : (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <StatusFilterChips value={statusChip} counts={statusCounts} onChange={setStatusChip} blocked={incompatibleChips} />
      <TraceColumnsPicker value={visibleColumns} onChange={setVisibleColumns} />
    </Stack>
  );

  return (
    <>
      <SectionCard title={title} action={action}>
        {isLoading ? (
          <EmptyState icon="solar:hourglass-linear" title="Loading calls…" />
        ) : groupableShown.length === 0 ? (
          <EmptyState
            icon="solar:filter-linear"
            title={failureGrouping && shown.length > 0 ? "Nothing to group here" : "No calls match that filter"}
            body={failureGrouping && shown.length > 0
              ? "This grouping only shows failing calls — none of the matching calls failed. Switch the status filter or group by Goal."
              : undefined}
          />
        ) : (
          <TraceTable
            tasks={shown}
            evals={evals}
            selected={selected}
            groupBy={groupBy}
            columns={visibleColumns}
            onToggle={toggle}
            onToggleAll={() => setSelected((prev) => (shown.every((t) => prev.has(t.id)) ? new Set() : new Set(shown.map((t) => t.id))))}
            onOpen={onOpenCall}
          />
        )}
      </SectionCard>

      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={applyFilters}
        aiPlaceholder="Ask AI — e.g. 'show calls that failed the refund eval'"
        placement="bottom-start"
      />
    </>
  );
}
RunTraceTable.propTypes = {
  executionId: PropTypes.string,
  onOpenCall: PropTypes.func,
  onFailedCriticalChange: PropTypes.func,
  onRerun: PropTypes.func,
};
