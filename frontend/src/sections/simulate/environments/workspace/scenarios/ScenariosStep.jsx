import { useEffect, useMemo, useState } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import { listScenarios } from "src/api/simulate-environments/scenarios";
import { useAmendScenarios } from "src/api/simulate-environments/scenariosHooks";
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
import { injectComposerScaffold } from "../../buildEnvironment/console/composerScaffoldBus";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "./scenarios.shapes";
import useScenarioPage, { PAGE_SIZE } from "./useScenarioPage";
import { useHarnessScenarios } from "src/api/simulate-environments/scenariosHooks";
import useSelection from "./useSelection";
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
export default function ScenariosStep({ env, envState, patch, locked = false, onStartRun, canRun = false }) {
  const { enqueueSnackbar } = useSnackbar();
  const amend = useAmendScenarios(env?.id);
  const [view, setView] = useState("table");
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
  const setGroupBy = (next) => { setGroupByRaw(next); setHiddenGroupIds([]); };
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
  const pageData = useScenarioPage({
    jobId: env?.id,
    search: query,
    filters,
    groupBy,
    page,
    pageSize: PAGE_SIZE,
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
  const hasScenarios = suiteTotal > 0;
  // A changed query moves the matching set, invalidating both the page position
  // and the selection predicate — reset both when it changes.
  const queryKey = JSON.stringify({ query, filters, groupBy });
  useEffect(() => {
    setPage(0);
    sel.clearRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey]);

  // The filter catalogue comes straight from the server (`fields`), counted
  // over the searched suite so an OR stays buildable; it drops into the shared
  // FilterPanel unchanged.
  const filterFields = pageData.fields;
  // The editor's background-noise choices come from the server field catalogue
  // (the values the agent actually uses), not a hardcoded list — so the picker
  // matches the suite and follows any backend vocabulary change automatically.
  const noiseOptions = useMemo(
    () => (pageData.fields || []).find((f) => f.value === "background_noise")?.choices ?? [],
    [pageData.fields],
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

  const clearFilters = () => { setQuery(""); setFilters({}); setHiddenGroupIds([]); };

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

  // The concrete id list a bulk action targets. include-mode is the picked ids;
  // all-mode is every scenario minus the user's exceptions. Phase 1 resolves
  // all-mode against `envState.scenarios` (the current mutation target); the
  // server-side predicate delete over the filtered suite lands with Phase 2's
  // amend route.
  const selectedIdsForAction = () => (
    sel.mode === "all"
      ? selected.filter((s) => !sel.idList.includes(s.id)).map((s) => s.id)
      : sel.idList
  );

  // Edit → hand the selection to the builder chat as a pinned scaffold, so the
  // user can add an instruction and send it as a bulk edit against those rows.
  const handleEditSelected = () => {
    const n = sel.count;
    injectComposerScaffold(
      `Edit the ${n} selected ${n === 1 ? "scenario" : "scenarios"}: `,
    );
  };

  // Run → start a run scoped to the selection × k. The target rides ?only=…&
  // trials=k; the product run page doesn't honour those yet (honest gap).
  const handleRunSelected = (k) => {
    onStartRun?.(selectedIdsForAction(), k || trials);
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

  // Enumerate the scenario names a bulk delete targets by paging the current
  // filter server-side (group_by="" so a page is a flat slice). include-mode
  // keeps the picked ids; all-mode keeps everything matching minus the user's
  // exceptions. Names — not ids — because the amend route resolves drops by name.
  const resolveSelectionNames = async () => {
    const wantAll = sel.mode === "all";
    const marked = new Set(sel.idList);
    const names = [];
    let p = 1;
    let totalPages = 1;
    do {
      // eslint-disable-next-line no-await-in-loop
      const res = await listScenarios(env?.id, {
        page: p,
        limit: 100,
        search: query,
        group_by: "",
        ...filters,
      });
      (res.results || []).forEach((r) => {
        const take = wantAll ? !marked.has(r.id) : marked.has(r.id);
        if (take) names.push(r.name);
      });
      totalPages = res.total_pages || 1;
      p += 1;
    } while (p <= totalPages);
    return names;
  };

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
      enqueueSnackbar("Couldn't delete — try again", { variant: "error" });
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
        onError: () => enqueueSnackbar("Couldn't delete — try again", { variant: "error" }),
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
        onError: () => enqueueSnackbar("Couldn't save — try again", { variant: "error" }),
      },
    );
  };

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
            {hasScenarios && (
              <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                {`(${suiteTotal.toLocaleString()})`}
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {SCENARIOS_COPY.subtitle}
          </Typography>
        </Box>
        {hasScenarios && <AddButton onClick={() => setAdding(true)} contained locked={locked} />}
      </Stack>

      {!hasScenarios ? (
        <RoutePlaceholder onAdd={() => setAdding(true)} locked={locked} />
      ) : (
        <>
          {/* Coverage leads the tab, collapsed — the summary numbers (Axes /
              Pairs / Forced) are visible on landing without scrolling past the
              list; the chevron unfurls the full breakdown. */}
          <Box sx={{ mb: 2 }}>
            <CoverageMatrix jobId={env?.id} search={query} filters={filters} />
          </Box>

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
                  groupBy={activeGroupBy}
                  onGroupByChange={setGroupBy}
                  groupings={pageData.groupings}
                  filterFields={filterFields}
                  filters={filters}
                  onApplyFilters={applyFilters}
                  filterCount={filterCount}
                  shownCount={pageData.total}
                  totalCount={suiteTotal}
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
                      onEdit={handleEditSelected}
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
            />
          </SectionCard>
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
