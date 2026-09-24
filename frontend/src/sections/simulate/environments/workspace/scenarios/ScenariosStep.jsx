import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { Box, Stack, Typography, Button } from "@mui/material";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { subTasksFor } from "src/api/simulate-environments/_fixtures/contract";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import ScenarioToolbar from "./ScenarioToolbar";
import SelectionBar from "./SelectionBar";
import CoverageMatrix from "./CoverageMatrix";
import AddScenariosDrawer from "./AddScenariosDrawer";
import ScenarioEditor from "./ScenarioEditor";
import PropTypes from "prop-types";
import {
  publishScenarioSelection,
  clearScenarioSelection,
} from "../../buildEnvironment/console/scenarioSelectionBus";
import { SCENARIOS_COPY, deriveUseCase } from "./scenarios.constants";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "./scenarios.shapes";
import useScenarioPage, { PAGE_SIZE } from "./useScenarioPage";
import useSelection from "./useSelection";
import { inflateScenarios, demoScaleFromSearch } from "./demoScenarios";
import PagedScenarioViews from "./PagedScenarioViews";

// A seeded-from-template env is read-only until forked; every mutating control
// carries this on its tooltip while locked.
const LOCK_TOOLTIP = "Fork this environment to edit.";

// The add CTA. Rendered in two places (header when the list is populated, and
// the empty placeholder), so it lives here as one node. Disabled with a
// coming-soon tooltip for now — the drawer it opens is built and wired, just
// not surfaced yet. On a locked template the same disabled button reads the
// fork tooltip instead. Wrapped in a span so the tooltip still fires over the
// disabled button.
function AddButton({ onClick, contained = false, locked = false }) {
  return (
    <CustomTooltip show arrow size="small" title={locked ? LOCK_TOOLTIP : SCENARIOS_COPY.addComingSoon}>
      <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
        <Button
          variant={contained ? "contained" : "outlined"}
          color="primary"
          size="small"
          disabled
          onClick={onClick}
          aria-label={SCENARIOS_COPY.addLabel}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
        >
          {SCENARIOS_COPY.addLabel}
        </Button>
      </Box>
    </CustomTooltip>
  );
}
AddButton.propTypes = { onClick: PropTypes.func, contained: PropTypes.bool, locked: PropTypes.bool };

// Shown when the environment has no scenarios at all — rare, since they are
// normally derived when the environment is built.
function RoutePlaceholder({ onAdd, locked = false }) {
  return (
    <EmptyState
      icon="solar:widget-add-linear"
      title={SCENARIOS_COPY.emptyTitle}
      body={SCENARIOS_COPY.emptyBody}
      action={<AddButton onClick={onAdd} contained locked={locked} />}
    />
  );
}
RoutePlaceholder.propTypes = { onAdd: PropTypes.func, locked: PropTypes.bool };

// Scenarios tab body. The scenarios are already here — derived when the
// environment is built — so the page leads with them, with the ways to add more
// beside the heading and per-row editing behind the pencil. Two views of the
// same rows share one toolbar so filters survive a view switch, and the coverage
// matrix below reads the live rows.
export default function ScenariosStep({ env, envState, patch, locked = false }) {
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const [view, setView] = useState("table");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState({});
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  // Grouping axis. Users read the scenarios differently depending on what
  // they're after: by goal for coverage, by persona to spot a caller type the
  // agent handles badly, by sub-goal to see which step everyone lands on.
  const [groupBy, setGroupByRaw] = useState("goal");
  // Hidden groups — click-to-hide directly on the group header. IDs are
  // dimension-specific, so switching the axis clears the hidden set.
  const [hiddenGroupIds, setHiddenGroupIds] = useState([]);
  const setGroupBy = (next) => { setGroupByRaw(next); setHiddenGroupIds([]); };
  const toggleGroupHidden = (id) => setHiddenGroupIds((prev) => (
    prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]
  ));

  const selected = useMemo(() => envState?.scenarios || [], [envState?.scenarios]);

  // ── Server-side pagination + predicate selection ─────────────────────────
  // The list is paged one page at a time through useScenarioPage — the seam a
  // real list endpoint drops into — and selection is a predicate (useSelection)
  // so "select all N matching" never needs every id loaded. `?scnDemo=<count>`
  // inflates the client rows to N synthetic ones so paging/select-all can be
  // exercised at scale (and turns on the request/payload inspector); a normal
  // env just pages its real rows with no simulated latency.
  const location = useLocation();
  const demoScale = demoScaleFromSearch(location.search);
  const allRows = useMemo(() => inflateScenarios(selected, demoScale), [selected, demoScale]);
  const [page, setPage] = useState(0);
  const pageData = useScenarioPage(
    allRows,
    { search: query, filters, groupBy, env, page },
    { limit: PAGE_SIZE, latencyMs: demoScale > 0 ? 400 : 0 },
  );
  const sel = useSelection(pageData.total);
  // A changed query moves the matching set, invalidating both the page position
  // and the selection predicate — reset both when it changes.
  const queryKey = JSON.stringify({ query, filters, groupBy });
  useEffect(() => {
    setPage(0);
    sel.clearRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey]);

  // filterFields — the shape the shared FilterPanel wants. Every dimension is an
  // enum whose choices come from the actual scenarios, so a template's own
  // personas / sub-goals show up rather than a hard-coded list. Use case reuses
  // the same derivation the table and coverage matrix read; its choices are the
  // opaque slug ids, surfaced through choiceLabels so the user only sees the
  // sentence.
  const useCaseOptions = useMemo(() => {
    const map = new Map();
    selected.forEach((r) => {
      const uc = deriveUseCase(r);
      if (!map.has(uc.id)) map.set(uc.id, uc.label);
    });
    return map;
  }, [selected]);
  const personaChoices = useMemo(
    () => [...new Set(selected.map((r) => r?.persona?.name).filter(Boolean))].sort(),
    [selected],
  );
  const subgoalChoices = useMemo(() => {
    const s = new Set();
    selected.forEach((r) => subTasksFor(r, env).forEach((sg) => s.add(sg.label)));
    return [...s].sort();
  }, [selected, env]);
  const filterFields = useMemo(() => [
    {
      value: "useCase", label: "Use case", type: "enum",
      choices: [...useCaseOptions.keys()],
      choiceLabels: Object.fromEntries(useCaseOptions),
    },
    { value: "persona", label: "Persona", type: "enum", choices: personaChoices },
    { value: "subgoal", label: "Sub-goal", type: "enum", choices: subgoalChoices },
  ], [useCaseOptions, personaChoices, subgoalChoices]);

  const filterCount = Object.values(filters).reduce(
    (sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0),
    0,
  );

  // Filter output arrives from the shared FilterPanel as { field: [values] }
  // (Basic tab, with a `<field>_not` key for an "Is not" row) or as
  // [{ field, operator, value }] (Query tab). Flatten both to the
  // { field: [values] } shape the predicate below reads.
  const applyFilters = (result) => {
    if (!result) { setFilters({}); return; }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value) ? r.value : (r.value != null ? [r.value] : []);
        if (!values.length) return;
        const key = r.operator === "is_not" || r.operator === "not_equals" ? `${r.field}_not` : r.field;
        flat[key] = [...(flat[key] || []), ...values];
      });
      setFilters(flat);
    } else {
      setFilters(result);
    }
  };

  const matchesFilters = (r) => {
    const uc = deriveUseCase(r).id;
    if (filters.useCase?.length && !filters.useCase.includes(uc)) return false;
    if (filters.useCase_not?.length && filters.useCase_not.includes(uc)) return false;
    const persona = r?.persona?.name || null;
    if (filters.persona?.length && !filters.persona.includes(persona)) return false;
    if (filters.persona_not?.length && filters.persona_not.includes(persona)) return false;
    if (filters.subgoal?.length || filters.subgoal_not?.length) {
      const subgoals = subTasksFor(r, env).map((s) => s.label);
      if (filters.subgoal?.length && !filters.subgoal.some((s) => subgoals.includes(s))) return false;
      if (filters.subgoal_not?.length && filters.subgoal_not.some((s) => subgoals.includes(s))) return false;
    }
    return true;
  };

  const q = query.trim().toLowerCase();
  const shown = selected.filter((r) => {
    if (!matchesFilters(r)) return false;
    if (!q) return true;
    const hay = `${r.name || ""} ${r.summary || ""} ${r.title || ""} ${r.task || ""} ${r.useCase || ""}`.toLowerCase();
    return hay.includes(q);
  });
  // `shown` is the full matching set (client-side today; the backend's job
  // tomorrow). It feeds the all-mode bulk delete below; the visible page comes
  // from useScenarioPage. Hiding groups is a page-local visual toggle.
  const hiddenCount = pageData.pageGroups.filter((g) => hiddenGroupIds.includes(g.id)).length;

  const clearFilters = () => { setQuery(""); setFilters({}); setHiddenGroupIds([]); };
  const removeScenario = (id) => patch({ scenarios: selected.filter((s) => s.id !== id) });

  // Mirror the predicate selection into the workspace builder chat. include-mode
  // names the picked rows (looked up in allRows); all-mode can't enumerate the
  // match cheaply, so it publishes just the count and the chip reads
  // "all N matching". Keyed on a signature so it only fires on a real change.
  const selSig = `${sel.mode}:${sel.count}:${sel.idList.join(",")}`;
  useEffect(() => {
    if (sel.count === 0) { clearScenarioSelection(); return; }
    if (sel.mode === "all") {
      publishScenarioSelection({ ids: [], rows: [], count: sel.count, all: true });
    } else {
      const rows = allRows.filter((s) => sel.idList.includes(s.id));
      publishScenarioSelection({ ids: sel.idList, rows, count: sel.count });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selSig]);
  // Clear the bus on unmount so navigating off the tab doesn't leave a stale
  // "N selected" chip hanging in the chat.
  useEffect(() => () => clearScenarioSelection(), []);

  const bulkDelete = () => {
    if (sel.count === 0) return;
    // Demo rows are synthetic and not in the store, so a scale-harness delete
    // reports the request rather than mutating — the payload is what the real
    // backend deletes by (see the inspector).
    if (demoScale > 0) {
      const noun = sel.count === 1 ? "scenario" : "scenarios";
      enqueueSnackbar(
        `Would delete ${sel.count.toLocaleString()} ${noun} · POST /scenarios/bulk-delete`,
        { variant: "info", autoHideDuration: 5000 },
      );
      sel.clear();
      return;
    }
    const priorScenarios = selected;
    // include-mode: the picked ids. all-mode: every matching id minus the user's
    // exceptions — computed client-side here, but the identical predicate is
    // what a backend bulk-delete would run.
    const removedIds = sel.mode === "all"
      ? new Set(shown.filter((s) => !sel.idList.includes(s.id)).map((s) => s.id))
      : new Set(sel.idList);
    const removed = priorScenarios.filter((s) => removedIds.has(s.id));
    patch({ scenarios: priorScenarios.filter((s) => !removedIds.has(s.id)) });
    sel.clear();
    const label = removed.length === 1
      ? `Removed "${removed[0].name || removed[0].title || "scenario"}"`
      : `Removed ${removed.length} scenarios`;
    const key = enqueueSnackbar(label, {
      variant: "info",
      autoHideDuration: 6000,
      action: (id) => (
        <Button
          size="small"
          sx={{ color: "common.white", fontWeight: 700, typography: "s2" }}
          onClick={() => {
            // Restore the exact pre-delete list captured in the closure, so the
            // rows come back in the order the user was looking at.
            patch({ scenarios: priorScenarios });
            closeSnackbar(id || key);
          }}
        >
          Undo
        </Button>
      ),
    });
  };

  // Adds dedupe against what is already on the environment, so re-adding a row
  // the drawer still had selected is a no-op rather than a duplicate.
  const addScenarios = (rows) => {
    const existing = new Set(selected.map((s) => s.id));
    const fresh = (rows || []).filter((r) => !existing.has(r.id));
    if (fresh.length) patch({ scenarios: [...selected, ...fresh] });
  };

  // Edits replace the row in place, so a scenario keeps its id and everything
  // keyed off it — coverage, run history, the evals mapped to it.
  const saveScenario = (row) =>
    patch({ scenarios: selected.map((s) => (s.id === row.id ? row : s)) });

  return (
    <Box sx={{ p: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "center" }}
        spacing={1.5}
        sx={{ mb: 2 }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
              {SCENARIOS_COPY.heading}
            </Typography>
            {selected.length > 0 && (
              <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                {`(${allRows.length.toLocaleString()})`}
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {SCENARIOS_COPY.subtitle}
          </Typography>
        </Box>
        {selected.length > 0 && <AddButton onClick={() => setAdding(true)} contained locked={locked} />}
      </Stack>

      {selected.length === 0 ? (
        <RoutePlaceholder onAdd={() => setAdding(true)} locked={locked} />
      ) : (
        <>
          <SectionCard sx={{ mb: 2 }}>
            {/* The bulk-action bar OVERLAYS the toolbar's own row while a
                selection is active — the toolbar stays mounted underneath and
                keeps defining the row height, so it's exactly the same height
                with or without a selection: zero layout shift, the header never
                moves. Clearing the selection reveals the toolbar again. */}
            <Box sx={{ position: "relative" }}>
              <Box aria-hidden={!locked && sel.count > 0 ? true : undefined}>
                <ScenarioToolbar
                  query={query}
                  onQueryChange={setQuery}
                  view={view}
                  onViewChange={setView}
                  groupBy={groupBy}
                  onGroupByChange={setGroupBy}
                  filterFields={filterFields}
                  filters={filters}
                  onApplyFilters={applyFilters}
                  filterCount={filterCount}
                  shownCount={pageData.total}
                  totalCount={allRows.length}
                  hiddenCount={hiddenCount}
                  onClear={clearFilters}
                />
              </Box>
              {!locked && sel.count > 0 && (
                <Box
                  sx={{
                    position: "absolute", inset: 0, zIndex: 2,
                    display: "flex", alignItems: "center", px: 2.5,
                    bgcolor: "background.paper",
                    borderBottom: "1px solid", borderColor: "divider",
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <SelectionBar
                      count={sel.count}
                      onDelete={bulkDelete}
                      onClear={sel.clear}
                      matching={{
                        mode: sel.mode,
                        total: pageData.total,
                        pageCount: pageData.pageIds.length,
                        onSelectAll: sel.selectAllMatching,
                      }}
                    />
                  </Box>
                </Box>
              )}
            </Box>

            <PagedScenarioViews
              pageData={pageData}
              selection={sel}
              env={env}
              view={view}
              hiddenGroupIds={hiddenGroupIds}
              onHideGroup={toggleGroupHidden}
              onEdit={setEditing}
              onRemove={removeScenario}
              page={page}
              onPageChange={setPage}
              query={query}
              filters={filters}
              groupBy={groupBy}
              showInspector={demoScale > 0}
              locked={locked}
            />
          </SectionCard>

          <CoverageMatrix scenarios={selected} env={env} />
        </>
      )}

      <AddScenariosDrawer
        open={adding}
        onClose={() => setAdding(false)}
        env={env}
        selected={selected}
        onAdd={addScenarios}
      />
      <ScenarioEditor
        open={!!editing}
        onClose={() => setEditing(null)}
        row={editing}
        env={env}
        onSave={saveScenario}
      />
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE.isRequired,
  patch: PropTypes.func.isRequired,
  locked: PropTypes.bool,
};
