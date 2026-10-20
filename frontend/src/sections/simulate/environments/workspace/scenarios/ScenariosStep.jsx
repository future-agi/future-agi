import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Typography, Button } from "@mui/material";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import { resolveScenarioSelection } from "src/api/simulate-environments/scenarioSelection";
import { useAmendScenarios } from "src/api/simulate-environments/scenariosHooks";
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
import { SCENARIOS_COPY } from "./scenarios.constants";
import { withServedLabels } from "./scenarioEditor.constants";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "./scenarios.shapes";
import useScenarioPage, { PAGE_SIZE } from "./useScenarioPage";
import { isScenarioSampleMode, SAMPLE_PAGE_SIZE } from "src/api/simulate-environments/scenariosSampleMode";
import { useHarnessScenarios } from "src/api/simulate-environments/scenariosHooks";
import { useDebounce } from "src/hooks/use-debounce";
import useSelection from "./useSelection";
import PagedScenarioViews from "./PagedScenarioViews";

// A seeded-from-template env is read-only until forked; every mutating control
// carries this on its tooltip while locked.
const LOCK_TOOLTIP = "Fork this environment to edit.";

// The add CTA. Rendered in two places (the toolbar when the list is populated, and
// the empty placeholder), so it lives here as one node. Disabled with a
// coming-soon tooltip for now — the drawer it opens is built and wired, just
// not surfaced yet. On a locked template the same disabled button reads the
// fork tooltip instead. Wrapped in a span so the tooltip still fires over the
// disabled button.
// `short` is the toolbar form: the list it sits on says what gets added, so the
// visible label drops the noun and the accessible name keeps it.
function AddButton({ onClick, contained = false, locked = false, short = false }) {
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
          // In the toolbar it matches the 32px row and Coverage's padding beside it.
          sx={{ typography: "s2", fontWeight: "fontWeightBold", ...(short && { height: 32, px: 1.75 }) }}
        >
          {short ? SCENARIOS_COPY.addShortLabel : SCENARIOS_COPY.addLabel}
        </Button>
      </Box>
    </CustomTooltip>
  );
}
AddButton.propTypes = {
  onClick: PropTypes.func,
  contained: PropTypes.bool,
  locked: PropTypes.bool,
  short: PropTypes.bool,
};

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
// environment is built — so the page leads with them: one toolbar (search,
// filter, display, coverage, add) over the list, per-row editing behind the
// pencil. Two views of the same rows share the toolbar so filters survive a
// view switch, and coverage reads the same search and filters as the list.
export default function ScenariosStep({ env, envState, patch, locked = false, onStartRun, canRun = false }) {
  const { enqueueSnackbar } = useSnackbar();
  const amend = useAmendScenarios(env?.id);
  const [view, setView] = useState("table");
  const [rowHeight, setRowHeight] = useState("Short");
  // Repeats (k) for a selection run — how many times each selected scenario is
  // re-run. Lives here (the selection bar is presentational) and rides the run
  // URL as ?trials=k. Default single-shot.
  const [trials, setTrials] = useState(1);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState({});
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  // A pending delete awaiting confirmation. Server deletes have no undo (there is
  // no create route to restore from), so a drop is confirmed first, then run.
  // { count, resolve: () => Promise<string[]> } — resolve yields the names to drop.
  const [pendingDelete, setPendingDelete] = useState(null);
  // Grouping axis. Users read the scenarios differently depending on what
  // they're after: by goal for coverage, by persona to spot a caller type the
  // agent handles badly, by sub-goal to see which step everyone lands on. Left
  // undefined until the user picks one, so the server's default (goal) drives
  // the initial view without writing that choice into local state (which would
  // trip the query-reset effect below on first load).
  const [groupBy, setGroupByRaw] = useState(undefined);
  // Hidden groups — click-to-hide directly on the group header. IDs are
  // dimension-specific, so switching the axis clears the hidden set.
  const [hiddenGroupIds, setHiddenGroupIds] = useState([]);
  const setGroupBy = (next) => { setGroupByRaw(next); setHiddenGroupIds([]); resetView(); };
  const handleQueryChange = (next) => { setQuery(next); resetView(); };
  const toggleGroupHidden = (id) => setHiddenGroupIds((prev) => (
    prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]
  ));

  const selected = useMemo(() => envState?.scenarios || [], [envState?.scenarios]);

  // ── Server-side pagination + predicate selection ─────────────────────────
  // The list is read one page at a time from the harness scenarios endpoint
  // through useScenarioPage — search, filters, grouping and page are all query
  // params the server answers — and selection is a predicate (useSelection) so
  // "select all N matching" never needs every id loaded.
  const [page, setPage] = useState(0);
  // Debounce the term that drives the fetch so typing doesn't fire a request
  // per keystroke; the input itself stays on the immediate `query` state.
  const debouncedQuery = useDebounce(query, 300);
  // Sample mode (?scnSample) pages the 20-row captured suite in smaller pages so
  // the pager is exercisable; the live default is PAGE_SIZE (25).
  const pageSize = isScenarioSampleMode() ? SAMPLE_PAGE_SIZE : PAGE_SIZE;
  const pageData = useScenarioPage({
    jobId: env?.id,
    search: debouncedQuery,
    filters,
    groupBy,
    page,
    pageSize,
  });
  // The grouping the server actually applied (its default until the user picks).
  const activeGroupBy = groupBy ?? pageData.groupBy;
  const sel = useSelection(pageData.total);
  // The unfiltered suite size. The heading, the empty-state gate and the
  // toolbar's "N of M" want the whole suite — the filtered list `total` shrinks
  // under a search/filter, and the bootstrap `envState.scenarios` goes stale
  // after an amend. A tiny page-0/limit-1 query with no search/filters returns
  // just the count and refetches with the list after an amend (shared key
  // prefix). Falls back to the bootstrap length until it resolves.
  const suiteQuery = useHarnessScenarios({ jobId: env?.id, page: 0, pageSize: 1 });
  const suiteTotal = suiteQuery.data?.total ?? selected.length;
  // The list polls every 15s. A failed first load takes the whole tab; a failed
  // background refetch keeps the rows already on screen and says so inline.
  const loadFailed = pageData.isError || suiteQuery.isError;
  const retryLoad = () => {
    if (pageData.isError) pageData.refetch();
    if (suiteQuery.isError) suiteQuery.refetch();
  };
  const hasScenarios = suiteTotal > 0;
  // A change to the search, the filters or the grouping moves the matching set,
  // so the page position and the selection predicate are reset in each change
  // handler below (resetView) — not in an effect, which needed an exhaustive-deps
  // disable and reset a tick late.
  const resetView = () => {
    setPage(0);
    sel.clearRef.current();
  };

  // Deleting the last rows of the last page shrinks the suite, so the current
  // page can fall past the new last page — the server then 404s that page and
  // the pager loops. Clamp back onto the last real page once the refetched
  // count is in.
  useEffect(() => {
    if (!pageData.loading && page > 0 && page >= pageData.pageCount) {
      setPage(Math.max(0, pageData.pageCount - 1));
    }
  }, [page, pageData.pageCount, pageData.loading]);

  // The filter catalogue comes straight from the server (`fields`), counted
  // over the searched suite so an OR stays buildable; it drops into the shared
  // FilterPanel unchanged.
  const filterFields = useMemo(
    () => withServedLabels(pageData.fields || [], pageData.levelLabels),
    [pageData.fields, pageData.levelLabels],
  );
  // The editor offers every noise bed the backend serves, not only the ones the suite already uses.
  const noiseOptions = useMemo(
    () => pageData.scenarioEditing?.noise_choices ?? [],
    [pageData.scenarioEditing],
  );

  const filterCount = Object.values(filters).reduce(
    (sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0),
    0,
  );

  // Filter output arrives from the shared FilterPanel as { field: [values] }
  // (Basic tab, with a `<field>_not` key for an "Is not" row) or as
  // [{ field, operator, value }] (Query tab). Flatten both to the
  // { field: [values] } shape the predicate below reads.
  const applyFilters = (result) => {
    resetView();
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

  // Hiding groups is a page-local visual toggle.
  const hiddenCount = pageData.pageGroups.filter((g) => hiddenGroupIds.includes(g.id)).length;

  const filtered = query.length > 0 || filterCount > 0 || hiddenCount > 0;
  const clearFilters = () => { setQuery(""); setFilters({}); setHiddenGroupIds([]); resetView(); };

  // Surface an amend's receipts: a refusal (a field that is proved, not
  // described) wins the message with its `why`; otherwise the success label.
  const surfaceReceipts = (data, successLabel) => {
    const refused = (data?.receipts || []).filter((r) => r.outcome === "refused");
    if (refused.length) {
      enqueueSnackbar(refused[0].why || `${refused.length} change(s) refused`, {
        variant: "warning",
        autoHideDuration: 8000,
      });
      return;
    }
    enqueueSnackbar(successLabel, { variant: "success", autoHideDuration: 4000 });
  };

  // Row trash → confirm, then drop that one scenario by name.
  const removeScenario = (id) => {
    const name = pageData.rows.find((r) => r.id === id)?.name;
    if (!name) return;
    setPendingDelete({ count: 1, resolve: async () => [name] });
  };

  // Resolve the selection against the current filter, server-side. The
  // selection holds row ids; `pick` projects each taken row.
  const resolveSelection = (pick) =>
    resolveScenarioSelection(env?.id, {
      search: debouncedQuery,
      filters,
      mode: sel.mode,
      marked: sel.idList,
      pick,
    });

  // Run → start a run scoped to the selection × k. The run route reads
  // scenario keys, not row ids, so the selection is resolved to keys first.
  const handleRunSelected = async (k) => {
    let keys;
    try {
      keys = await resolveSelection((r) => r.scenario_key);
    } catch {
      enqueueSnackbar("Couldn't start the run. Try again", { variant: "error" });
      return;
    }
    if (keys.length) onStartRun?.(keys, k || trials);
  };

  // Mirror the predicate selection into the workspace builder chat. include-mode
  // names the picked rows; all-mode can't enumerate the match cheaply, so it
  // publishes just the count and the chip reads "all N matching". Keyed on a
  // signature so it only fires on a real change.
  const selSig = `${sel.mode}:${sel.count}:${sel.idList.join(",")}`;
  useEffect(() => {
    if (sel.count === 0) { clearScenarioSelection(); return; }
    if (sel.mode === "all") {
      publishScenarioSelection({ ids: [], rows: [], count: sel.count, all: true });
    } else {
      const rows = selected.filter((s) => sel.idList.includes(s.id));
      publishScenarioSelection({ ids: sel.idList, rows, count: sel.count });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selSig]);
  // Clear the bus on unmount so navigating off the tab doesn't leave a stale
  // "N selected" chip hanging in the chat.
  useEffect(() => () => clearScenarioSelection(), []);

  // The scenario names a bulk delete targets. Names — not ids — because the
  // amend route resolves drops by name.
  const resolveSelectionNames = () => resolveSelection((r) => r.name);

  // Bulk delete → confirm, then one drop naming the whole selection.
  const bulkDelete = () => {
    if (sel.count === 0) return;
    setPendingDelete({ count: sel.count, resolve: resolveSelectionNames });
  };

  // Run the confirmed delete: resolve the names, then a single amend `drop`.
  const confirmDelete = async () => {
    const pending = pendingDelete;
    setPendingDelete(null);
    if (!pending) return;
    let names;
    try {
      names = await pending.resolve();
    } catch {
      enqueueSnackbar("Couldn't delete. Try again", { variant: "error" });
      return;
    }
    if (!names.length) return;
    amend.mutate(
      { rework: true, changes: [{ op: "drop", scenarios: names }] },
      {
        onSuccess: (data) => {
          sel.clear();
          surfaceReceipts(
            data,
            names.length === 1 ? "Deleted 1 scenario" : `Deleted ${names.length} scenarios`,
          );
        },
        onError: () => enqueueSnackbar("Couldn't delete. Try again", { variant: "error" }),
      },
    );
  };

  // Adds dedupe against what is already on the environment, so re-adding a row
  // the drawer still had selected is a no-op rather than a duplicate.
  const addScenarios = (rows) => {
    const existing = new Set(selected.map((s) => s.id));
    const fresh = (rows || []).filter((r) => !existing.has(r.id));
    if (fresh.length) patch({ scenarios: [...selected, ...fresh] });
  };

  // Edits route through the amend route as set_field / set_persona ops (the
  // editor emits only the changed writable fields). A refused receipt surfaces
  // its `why`; a success invalidates the list + coverage so the row updates.
  const saveScenario = ({ changes, rework }) => {
    if (!changes?.length) return;
    amend.mutate(
      { rework, changes },
      {
        onSuccess: (data) => surfaceReceipts(data, "Saved"),
        onError: () => enqueueSnackbar("Couldn't save. Try again", { variant: "error" }),
      },
    );
  };

  return (
    // The tab's height: the toolbar keeps its size and the table's box fills the
    // rest, scrolling on its own. It never drops below a few rows; on a short
    // panel the panel scrolls instead.
    <Box sx={{ p: 2, height: "100%", display: "flex", flexDirection: "column" }}>

      {loadFailed && !pageData.rows.length ? (
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Couldn't load scenarios"
          body="Something went wrong loading this environment's scenarios. Try again."
        />
      ) : !hasScenarios ? (
        <RoutePlaceholder onAdd={() => setAdding(true)} locked={locked} />
      ) : (
        <>
          {loadFailed && (
            <Alert
              severity="error"
              sx={{ mb: 2, typography: "s3", flexShrink: 0 }}
              action={(
                <Button color="inherit" size="small" onClick={retryLoad}>
                  Retry
                </Button>
              )}
            >
              Couldn&apos;t refresh the scenarios. Showing the last loaded list.
            </Alert>
          )}
          <Box sx={{ flex: 1, minHeight: 320, minWidth: 0, display: "flex", flexDirection: "column" }}>
            {/* The bulk-action bar covers the toolbar's row while a selection is
                active. Both sit in one grid cell, the toolbar staying mounted
                underneath, so the row keeps the toolbar's height (no shift when
                selecting) and only grows when the bar needs more, never
                clipping its buttons. Clearing the selection reveals the toolbar. */}
            <Box sx={{ display: "grid", flexShrink: 0, "& > *": { gridArea: "1 / 1", minWidth: 0 } }}>
              <Box aria-hidden={!locked && sel.count > 0 ? true : undefined}>
                <ScenarioToolbar
                  query={query}
                  onQueryChange={handleQueryChange}
                  view={view}
                  onViewChange={setView}
                  rowHeight={rowHeight}
                  onRowHeightChange={setRowHeight}
                  groupBy={activeGroupBy}
                  onGroupByChange={setGroupBy}
                  groupings={pageData.groupings}
                  filterFields={filterFields}
                  filters={filters}
                  onApplyFilters={applyFilters}
                  filterCount={filterCount}
                  actions={(
                    <>
                      <CoverageMatrix jobId={env?.id} search={debouncedQuery} filters={filters} />
                      <AddButton onClick={() => setAdding(true)} contained locked={locked} short />
                    </>
                  )}
                />
              </Box>
              {!locked && sel.count > 0 && (
                <Box
                  sx={{
                    // No padding here: SelectionBar carries the toolbar's own
                    // insets, so its controls land on the toolbar's edges.
                    zIndex: 2,
                    display: "flex", alignItems: "flex-start",
                    bgcolor: "background.paper",
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <SelectionBar
                      count={sel.count}
                      onDelete={bulkDelete}
                      onClear={sel.clear}
                      onRun={canRun && onStartRun ? handleRunSelected : undefined}
                      trials={trials}
                      onTrialsChange={setTrials}
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

            {/* The table's own bordered box, its edges on the toolbar's. */}
            <Box
              sx={{
                flex: 1, minHeight: 0, display: "flex", flexDirection: "column",
                border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden",
              }}
            >
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
                groupBy={activeGroupBy}
                locked={locked}
                rowHeight={rowHeight}
                readout={filtered ? {
                  shown: pageData.total,
                  total: suiteTotal,
                  hiddenCount,
                  onlyGroupsHidden: hiddenCount > 0 && query.length === 0 && filterCount === 0,
                  onClear: clearFilters,
                } : undefined}
              />
            </Box>
          </Box>
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
        onSave={saveScenario}
        scenarioEditing={pageData.scenarioEditing}
        noiseOptions={noiseOptions}
        levelLabels={pageData.levelLabels}
      />
      <ConfirmDialog
        open={!!pendingDelete}
        onClose={() => setPendingDelete(null)}
        title="Delete scenarios?"
        content={
          <Typography component="span" sx={{ typography: "s2" }}>
            {pendingDelete?.count === 1
              ? "This scenario will be permanently removed. This can't be undone."
              : `${pendingDelete?.count ?? 0} scenarios will be permanently removed. This can't be undone.`}
          </Typography>
        }
        action={
          <Button size="small" variant="contained" color="error" onClick={confirmDelete}>
            Delete
          </Button>
        }
      />
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE.isRequired,
  patch: PropTypes.func.isRequired,
  locked: PropTypes.bool,
  // Starts a run scoped to the selected scenarios × trials — (ids, trials).
  onStartRun: PropTypes.func,
  // Whether the env is runnable; gates the selection bar's Run button.
  canRun: PropTypes.bool,
};
