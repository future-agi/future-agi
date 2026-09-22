import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSnackbar } from "notistack";
import {
  Box, Stack, Typography, Button, Tooltip, IconButton, Tab,
  TextField, Popover, Checkbox, InputBase, Menu, MenuItem, ListItemIcon,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { alpha } from "@mui/material/styles";
import { SectionCard, PersonaBadge } from "../components/primitives";
import { generatedPool } from "../_mock/scenarios";
import { staleScenarios, proofStatus, reproved, autoReprove, brokenScenarios, markEdited, INVALIDATING } from "../_mock/proofs";
import { subTasksFor } from "../_mock/contract";
import ScenarioDetail from "../components/ScenarioDetail";
import CoverageMatrix from "./scenarios/CoverageMatrix";
import AddScenariosDrawer from "./scenarios/AddScenariosDrawer";
import ScenarioEditor from "./scenarios/ScenarioEditor";
import ScenarioTable from "./scenarios/ScenarioTable";
import SelectionBar from "./scenarios/SelectionBar";
import RecentAdditionsStrip from "./scenarios/RecentAdditionsStrip";
import GateRejects from "./scenarios/GateRejects";
import { PickRouteIllustration } from "./scenarios/RouteThumbs";
import {
  publishScenarioSelection,
  clearScenarioSelection,
  subscribeScenarioSelection,
  getScenarioSelection,
} from "../_mock/scenarioSelectionBus";
import { injectComposerScaffold } from "../_mock/composerScaffoldBus";
import { stampProvenance, defaultBatchId, ensureProvenance, provenanceLabel, sourceOf, groupByBatch, relativeTime } from "../_mock/scenarioProvenance";

/* AddScenariosDrawer emits `route` ids (twin | production | dataset |
   script); provenance vocab is different, so translate at the boundary. */
const SOURCE_MAP = {
  twin: "template",
  production: "production",
  dataset: "dataset-import",
  script: "manual",
  generate: "builder-chat",
};
import { FilterPanel } from "src/components/filter-panel";


/**
 * Scenarios.
 *
 * The scenarios are already here: they are derived from the agent when the
 * environment is built. So the page leads with them, and the ways to add more
 * live behind a button — five route cards across the top said the opposite,
 * that nothing had happened yet and a route had to be chosen first.
 *
 * Two views of the same rows. The list expands one scenario into its brief,
 * its checks and the proof it is passable. The table puts thirty of them side
 * by side on the derived axes, which is the view you want when the question is
 * what is in here rather than what is this.
 */
export default function ScenariosStep({ env, envState, patch, buildMode, onBuilderPrompt, locked = false, onFork }) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  /* Default to the table view — it's the denser, more scannable
     shape and it's what the product team wanted users to land on. */
  const [view, setView] = useState("table");
  /*
    Search + use-case filter are shared by both views now. They used to
    live inside the list-only toolbar, which meant switching to the
    table lost your filters. Lifting to the parent lets the toolbar sit
    between the page heading and either body, and both views read the
    same `shown` rows.
  */
  const [query, setQuery] = useState("");
  /* Hidden groups — click-to-hide directly on the group header replaces
     the old filter modal. IDs are dimension-specific (a "persona:polite"
     id means nothing under Goal grouping) so switching Group by clears
     the hidden set. */
  const [hiddenGroupIds, setHiddenGroupIds] = useState([]);
  /* When set, the list narrows to just the broken scenarios. Turned on by
     the red banner's "Show them" button so a user can go from "N broke"
     straight to those exact rows without hunting through 68. */
  const [focusBroken, setFocusBroken] = useState(false);
  /* Row-level facet filter — same shape the rest of the product uses
     (Runs list, Improvements list). Four dimensions, each a multi-select. */
  const [filters, setFilters] = useState({});
  const [filterAnchor, setFilterAnchor] = useState(null);
  const filterCount = Object.values(filters).reduce(
    (sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0),
    0,
  );
  /*
    Filter output arrives from the shared FilterPanel as either
    { field: [values] } (Basic tab) or [{field, operator, value}]
    (Query tab). Flatten both to { field: [values] } to match the
    predicate below.
  */
  const applyFilters = (result) => {
    if (!result) { setFilters({}); return; }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value) ? r.value : (r.value != null ? [r.value] : []);
        if (!values.length) return;
        flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      setFilters(result);
    }
  };
  /* Grouping mode — matches the trace-table pattern from the run view.
     Users read the scenarios differently depending on what they're
     debugging: by goal for coverage, by persona to spot a caller type
     the agent handles badly, by sub-goal to see which step everyone
     lands on. */
  const [groupBy, setGroupByRaw] = useState("goal");
  const [groupByAnchor, setGroupByAnchor] = useState(null);
  const setGroupBy = (next) => { setGroupByRaw(next); setHiddenGroupIds([]); };
  const toggleGroupHidden = (id) => setHiddenGroupIds((prev) => (
    prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]
  ));
  const selected = envState?.scenarios || [];

  const q = query.trim().toLowerCase();

  /* Row-facet extractors — each returns the id(s) a scenario belongs to
     for one filter dimension. Kept as pure helpers so the popover can
     use the same functions to count how many scenarios each option
     covers. */
  const statusFacets = (r) => {
    const ids = [];
    if (r.provedBroke) ids.push("Broken");
    if (r.critical) ids.push("Critical");
    if (proofStatus(r, env, envState).edited) ids.push("Edited");
    return ids;
  };
  const personaFacet = (r) => r?.persona?.name || null;
  const KIND_LABEL = { rule: "Rule test", trap: "Trap", adversarial: "Adversarial", edge: "Edge case", happy: "Happy path" };
  const kindFacet = (r) => {
    const id = String(r?.id || "");
    if (id.includes("-rule-")) return KIND_LABEL.rule;
    if (id.includes("-trap-")) return KIND_LABEL.trap;
    if (id.includes("-adversarial-")) return KIND_LABEL.adversarial;
    if (id.includes("-edge-")) return KIND_LABEL.edge;
    return KIND_LABEL.happy;
  };
  const subgoalFacet = (r) => {
    const subs = subTasksFor(r, env);
    return subs?.[0]?.label || null;
  };

  const matchesFilters = (r) => {
    if (filters.status?.length) {
      const ids = statusFacets(r);
      if (!filters.status.some((s) => ids.includes(s))) return false;
    }
    if (filters.persona?.length && !filters.persona.includes(personaFacet(r))) return false;
    if (filters.kind?.length && !filters.kind.includes(kindFacet(r))) return false;
    if (filters.subgoal?.length && !filters.subgoal.includes(subgoalFacet(r))) return false;
    return true;
  };

  /*
    filterFields — the shape the shared FilterPanel wants. Every
    dimension is an enum with a fixed choice list. Persona and
    sub-goal come from the actual scenarios so a template's own
    names show up rather than a hard-coded list.
  */
  const personaChoices = useMemo(
    () => [...new Set((selected || []).map((r) => r?.persona?.name).filter(Boolean))].sort(),
    [selected],
  );
  const subgoalChoices = useMemo(
    () => {
      const s = new Set();
      (selected || []).forEach((r) => subTasksFor(r, env).forEach((sg) => s.add(sg.label)));
      return [...s].sort();
    },
    [selected, env],
  );
  const filterFields = useMemo(() => [
    { value: "status",  label: "Status",   type: "enum", choices: ["Broken", "Critical", "Edited"] },
    { value: "persona", label: "Persona",  type: "enum", choices: personaChoices },
    { value: "kind",    label: "Kind",     type: "enum", choices: Object.values(KIND_LABEL) },
    { value: "subgoal", label: "Sub-goal", type: "enum", choices: subgoalChoices },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [personaChoices, subgoalChoices]);

  const shown = selected.filter((r) => {
    if (focusBroken && !r.provedBroke) return false;
    if (!matchesFilters(r)) return false;
    if (!q) return true;
    const hay = `${r.name || ""} ${r.summary || ""} ${r.title || ""} ${r.task || ""} ${r.useCase || ""}`.toLowerCase();
    return hay.includes(q);
  });
  /* Groups first, then apply the hide list. Row-level narrowing lives in
     `shown` (search) and everything else — hiding — is expressed as
     "which groups to show" so the interaction stays purely visual. */
  const allGroups = groupScenarios(shown, groupBy, env);
  const shownGroups = allGroups.filter((g) => !hiddenGroupIds.includes(g.id));
  const hiddenCount = allGroups.length - shownGroups.length;

  /*
    Adding scenarios in real life isn't instant — every row goes
    through pre-verification (proved solvable, non-vacuous, pointed
    at a rule) before it lands on the environment. We stand in for
    that here with a scripted ~800ms delay so the "Adding…" toast
    reads as a real status, then swap it for the success toast
    once the patch actually fires. If none of the rows were new
    (all duplicates against what's already selected) we say so
    instead of claiming a nonexistent add.
  */
  const addScenarios = (rows, source) => {
    const existing = new Set(selected.map((s) => s.id));
    const fresh = (rows || []).filter((r) => !existing.has(r.id));
    const attempted = rows?.length || 0;
    const dupeCount = attempted - fresh.length;

    /* Toast up front, so the user knows the click landed even though
       the drawer is already sliding closed. */
    const pendingKey = enqueueSnackbar(
      attempted === 1 ? "Adding 1 scenario…" : `Adding ${attempted} scenarios…`,
      { variant: "info", persist: true },
    );

    setTimeout(() => {
      closeSnackbar(pendingKey);
      if (fresh.length === 0) {
        enqueueSnackbar(
          attempted === 1 ? "That scenario was already added." : "Those scenarios were already added.",
          { variant: "info" },
        );
        return;
      }
      /* Stamp provenance so the row provenance form + "Recent
         additions" strip can attribute this batch. The `source` arg
         from the drawer maps 1:1 onto our source vocabulary; if the
         drawer passes something we don't recognise, fall back to
         manual (the honest thing to say when a human clicked add). */
      const stampSource = SOURCE_MAP[source] || "manual";
      const stampAt = new Date().toISOString();
      const batchId = defaultBatchId(stampSource, stampAt);
      const fresh2 = fresh.map((r) => stampProvenance(r, { source: stampSource, at: stampAt, batchId }));
      patch({ scenarios: [...selected, ...fresh2], scenarioSource: source });
      const base = fresh.length === 1
        ? "1 scenario added"
        : `${fresh.length} scenarios added`;
      const suffix = dupeCount > 0
        ? ` · ${dupeCount} already on this environment`
        : "";
      enqueueSnackbar(`${base}${suffix}`, { variant: "success" });
    }, 800);
  };

  const removeScenario = (id) =>
    patch({ scenarios: selected.filter((s) => s.id !== id) });

  /*
    Bulk-action selection — checkboxes on the table are for acting on
    a group of rows. Source of truth is scenarioSelectionBus so the
    selection survives tab changes: a user can pick scenarios on
    the Scenarios tab, hop over to Evaluations to add an evaluator,
    and come back with the same rows still checked. This tab hydrates
    from the bus on mount instead of resetting to [].
  */
  const busSelection = useSyncExternalStore(
    subscribeScenarioSelection, getScenarioSelection, getScenarioSelection,
  );
  const selectedIds = useMemo(() => {
    const validIds = new Set((selected || []).map((s) => s.id));
    return (busSelection?.ids || []).filter((id) => validIds.has(id));
  }, [busSelection, selected]);
  const selectedRows = useMemo(
    () => (selected || []).filter((s) => selectedIds.includes(s.id)),
    [selected, selectedIds],
  );
  const handleSelectionChange = (ids) => {
    const rows = (selected || []).filter((s) => ids.includes(s.id));
    publishScenarioSelection({ ids, rows });
  };
  const clearSelection = () => {
    clearScenarioSelection();
  };

  /* Edit with builder — pins a Claude-style skill chip inside the
     composer instead of stuffing text into the draft. The chip's
     visible label stays compact ("Edit 2 scenarios"), while the
     full context (scenario names) rides along invisibly as the
     scaffold's prompt so the AI still knows what to act on. User
     types their instruction, the two get concatenated on send. */
  const handleEditSelected = () => {
    if (selectedRows.length === 0) return;
    const count = selectedRows.length;
    const names = selectedRows.map((r) => r.name).filter(Boolean);
    const preview = names.slice(0, 3).join(", ");
    const more = names.length > 3 ? `, +${names.length - 3} more` : "";
    injectComposerScaffold({
      label: `Edit ${count} scenario${count === 1 ? "" : "s"}`,
      prompt: `Edit these ${count} scenarios (${preview}${more}):`,
      icon: "solar:pen-2-linear",
    });
  };

  const bulkDelete = () => {
    if (selectedIds.length === 0) return;
    const removed = (selected || []).filter((s) => selectedIds.includes(s.id));
    const keptIds = new Set(selectedIds);
    patch({ scenarios: (selected || []).filter((s) => !keptIds.has(s.id)) });
    clearSelection();
    const label = removed.length === 1
      ? `Removed "${removed[0].name || removed[0].title || "scenario"}"`
      : `Removed ${removed.length} scenarios`;
    const key = enqueueSnackbar(label, {
      variant: "info",
      autoHideDuration: 6000,
      action: (id) => (
        <Button
          size="small" sx={{ color: "common.white", fontWeight: 700, typography: "s2" }}
          onClick={() => {
            /* Restore in original order — prepend the removed rows to
               whatever the store currently holds. Preserves the row
               order the user was looking at when they clicked delete. */
            patch({ scenarios: [...removed, ...(selected || []).filter((s) => !keptIds.has(s.id))] });
            closeSnackbar(id || key);
          }}
        >
          Undo
        </Button>
      ),
    });
  };

  /* Edits replace the row in place, so a scenario keeps its id and everything
     keyed off it — coverage, run history, the evals mapped to it.

     And an edited scenario is an unproved one: the proof was of the task as it
     read before. Stamping the edit is what makes it show up in the banner
     above rather than keeping a green tick it no longer earns. */
  const saveScenario = (row) =>
    patch({ scenarios: selected.map((s) => (s.id === row.id ? markEdited(row) : s)) });

  /* The chosen depth overrides the environment's own, and every generation
     route is handed the adjusted environment rather than the original. */
  const depth = envState.difficulty || env.difficulty || "Advanced";
  const genEnv = { ...env, difficulty: depth };

  /*
    Scenarios are proved against a version of the world, and this environment
    has moved since some of them were proved. Nothing about that breaks loudly
    — the runs still produce numbers — so it has to be said here.

    In buildMode this is suppressed: the environment was just derived, it is
    on v1 by definition and nothing has drifted. Showing "12 of 32 need
    re-proving · this environment is on v3" on a freshly-built environment
    was reading a workspace demo story into a screen where none of it had
    happened yet.
  */
  const stale = buildMode ? [] : staleScenarios(selected, env, envState);
  /* Broken subset from the most recent auto-re-prove pass — the ones the
     world change actually invalidated. `provedBroke` is stamped by
     `autoReprove` and stays on the scenario until the user edits/removes
     it, so this is stable across renders. */
  const broken = buildMode ? [] : brokenScenarios(selected);
  const brokenReasons = [...new Set(broken.flatMap((s) => s.brokeReasons || []))];

  /* Auto re-prove the moment we notice drift. Every scenario whose proof
     still holds gets restamped to the current env version; the ~20% that
     don't survive keep the old stamp plus a `provedBroke` flag so the
     banner and the row treatment can call them out. Deterministic per
     scenario id + env version, so a reload lands on the same result. */
  const autoRunRef = useRef(null);
  useEffect(() => {
    if (buildMode) return;
    if (stale.length === 0) return;
    const key = `${env.id}::${envState?.activeEnvVersion || "v1"}::${stale.length}`;
    if (autoRunRef.current === key) return;
    autoRunRef.current = key;
    patch({ scenarios: autoReprove(selected, env, envState) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildMode, env.id, envState?.activeEnvVersion, stale.length]);

  return (
    <Box sx={{ p: 2 }}>
      {/*
        Title and the add button on one line. Adding is a secondary action on
        this screen — the scenarios are already derived — so it is an outlined
        button beside the heading rather than five cards above it.
      */}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "center" }}
        spacing={1.5}
        sx={{ mb: 2 }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Scenarios</Typography>
            {selected.length > 0 && (
              <Typography sx={{ typography: "s1", fontWeight: 500, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                ({selected.length})
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Each scenario is one task your agent has to complete, and carries its own persona.
          </Typography>
        </Box>
        {/*
          Only show the header CTA once there are scenarios in the
          list. When it's empty, the RoutePlaceholder below already
          renders a prominent Add scenarios button — two CTAs for the
          same action read as noise (same pattern the Evaluations tab
          uses).
        */}
        {/* Hidden while any scenario is checked — the toolbar has
            transformed into the SelectionBar and "Add scenarios" is
            not a selection-scoped action, so it would just add
            visual noise beside "Run N selected". */}
        {selected.length > 0 && selectedIds.length === 0 && (
          <Tooltip arrow title={locked ? "Fork this environment to add scenarios." : ""}>
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={locked}
                onClick={() => setAdding(true)}
                startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
                sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
              >
                Add scenarios
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      {broken.length > 0 && (
        <Stack
          direction="row" alignItems="flex-start" spacing={1.5}
          sx={{
            mb: 2, px: 2.5, py: 1.75, borderRadius: 1.5, border: "1px solid",
            borderColor: alpha("#DC2626", 0.35),
            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.08 : 0.04),
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={16} sx={{ color: "#DC2626", flexShrink: 0, mt: "2px" }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>
              {broken.length} scenarios broke after the env changed
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              We re-checked them against {envState?.activeEnvVersion || "the current version"}. Fix, remove, or dismiss.
            </Typography>
          </Box>
          <Stack direction="row" spacing={0.75} sx={{ flexShrink: 0 }}>
            <Button
              variant="contained" color="primary" size="small"
              onClick={() => setFocusBroken(true)}
              startIcon={<Iconify icon="solar:eye-linear" width={14} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Show them
            </Button>
            <Button
              variant="outlined" size="small"
              onClick={() => {
                /* Keep the scenarios but drop the broken flag — user has
                   accepted the current state as OK and wants to keep them. */
                patch({ scenarios: selected.map((s) => (s.provedBroke ? { ...s, provedBroke: false, brokeReasons: [] } : s)) });
                setFocusBroken(false);
              }}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
            >
              Dismiss
            </Button>
          </Stack>
        </Stack>
      )}

      {/* Coverage summary — collapsed by default so users see the
          three numbers (Axes / Pairs / Forced) the moment they land
          on the tab, without the panel pushing the list down. Click
          to unfurl the full per-axis + pairwise + guardrails report.
          Previously this sat at the bottom of the tab, which meant
          users had to scroll past 80+ rows to know it existed. */}
      {selected.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <CoverageMatrix scenarios={selected} env={env} />
        </Box>
      )}

      {selected.length === 0 ? (
        <RoutePlaceholder env={genEnv} onAdd={() => setAdding(true)} />
      ) : (
        /*
          No title/subtitle on the section card — the page heading above
          already names this list. The card's top row is a shared
          toolbar (search · filter · list/table tabs) that both views
          read from, so filters survive a view switch.
        */
        <SectionCard sx={{ mb: 2, overflow: "visible" }}>
          {/*
            Sticky toolbar row. It pins to the top of the scenarios
            viewport as the user scrolls through 88+ rows, so when
            the row transforms into the SelectionBar the actions are
            always in reach — no floating pill over content, no
            scroll-hunting to find the toolbar. Same slot handles
            both states, which is why nothing feels "in a random
            place" any more.
          */}
          <Box sx={{
            position: "sticky", top: 0, zIndex: 3,
            bgcolor: "background.paper",
            borderBottom: "1px solid", borderColor: "divider",
          }}>
          {!locked && selectedIds.length > 0 ? (
            <SelectionBar
              count={selectedIds.length}
              onEdit={handleEditSelected}
              onDelete={bulkDelete}
              onClear={clearSelection}
            />
          ) : (
          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 2.5, py: 1.25 }}
          >
            <TextField
              size="small"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search scenarios by name, task or use case…"
              InputProps={{
                sx: { typography: "s2" },
                startAdornment: (
                  <Box sx={{ pr: 0.75, pl: 0.25, display: "flex", color: "text.subtitle" }}>
                    <Iconify icon="solar:magnifer-linear" width={14} />
                  </Box>
                ),
              }}
              sx={{ maxWidth: 380, flex: 1 }}
            />
            <Button
              size="small" variant="outlined"
              onClick={(e) => setGroupByAnchor(e.currentTarget)}
              startIcon={<Iconify icon={(SCENARIO_GROUPINGS.find((g) => g.id === groupBy) || SCENARIO_GROUPINGS[0]).icon} width={14} />}
              endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
              sx={{
                typography: "s2", fontWeight: 700, textTransform: "none",
                color: "text.primary", borderColor: "divider",
              }}
            >
              {`Group by · ${(SCENARIO_GROUPINGS.find((g) => g.id === groupBy) || SCENARIO_GROUPINGS[0]).label}`}
            </Button>
            <Popover
              open={!!groupByAnchor}
              anchorEl={groupByAnchor}
              onClose={() => setGroupByAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
              transformOrigin={{ vertical: "top", horizontal: "left" }}
              slotProps={{ paper: { sx: { minWidth: 200, p: 0.5, mt: 0.5 } } }}
            >
              {SCENARIO_GROUPINGS.map((g) => {
                const active = g.id === groupBy;
                return (
                  <Box
                    key={g.id}
                    onClick={() => { setGroupBy(g.id); setGroupByAnchor(null); }}
                    sx={{
                      display: "flex", alignItems: "center", gap: 1,
                      px: 1.25, py: 0.875, borderRadius: 0.75, cursor: "pointer",
                      bgcolor: active ? "action.hover" : "transparent",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Iconify icon={g.icon} width={14} sx={{ color: active ? "primary.main" : "text.subtitle" }} />
                    <Typography sx={{ typography: "s2", flex: 1, fontWeight: active ? 700 : 500 }}>
                      {g.label}
                    </Typography>
                    {active && <Iconify icon="eva:checkmark-fill" width={14} sx={{ color: "primary.main" }} />}
                  </Box>
                );
              })}
            </Popover>
            <Button
              size="small" variant="outlined"
              onClick={(e) => setFilterAnchor(e.currentTarget)}
              startIcon={<Iconify icon="mage:filter" width={14} />}
              endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
              sx={{
                typography: "s2", fontWeight: 700, textTransform: "none",
                color: filterCount ? "primary.main" : "text.primary",
                borderColor: filterCount ? "primary.main" : "divider",
              }}
            >
              Filter{filterCount ? ` (${filterCount})` : ""}
            </Button>
            <FilterPanel
              anchorEl={filterAnchor}
              open={!!filterAnchor}
              onClose={() => setFilterAnchor(null)}
              filterFields={filterFields}
              currentFilters={filters}
              onApply={applyFilters}
              aiPlaceholder="Ask AI — e.g. 'show me critical scenarios with the impatient persona'"
              placement="bottom-start"
            />
            {(q.length > 0 || hiddenCount > 0 || focusBroken || filterCount > 0) && (
              <>
                <Typography sx={{ typography: "s3", color: focusBroken ? "#DC2626" : "text.subtitle", whiteSpace: "nowrap" }}>
                  {focusBroken
                    ? `Showing ${shown.length} broken`
                    : `${shown.length} of ${selected.length}${hiddenCount > 0 ? ` · ${hiddenCount} group${hiddenCount === 1 ? "" : "s"} hidden` : ""}`}
                </Typography>
                <Button
                  size="small"
                  onClick={() => { setQuery(""); setHiddenGroupIds([]); setFocusBroken(false); setFilters({}); }}
                  sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
                >
                  {focusBroken || (hiddenCount > 0 && !q && !filterCount) ? "Show all" : "Clear"}
                </Button>
              </>
            )}
            <Box sx={{ flex: 1 }} />
            <SegmentedTabs value={view} onChange={(_, v) => setView(v)} sx={{ flexShrink: 0 }}>
              <Tab value="table" label="Table" />
              <Tab value="list" label="List" />
            </SegmentedTabs>
          </Stack>
          )}
          </Box>

          {shownGroups.length === 0 ? (
            <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {hiddenCount > 0 && q.length === 0
                  ? "Every group is hidden — click Show all to bring them back."
                  : "No scenarios match your search."}
              </Typography>
            </Box>
          ) : (
            <>
              {/* Batches are rendered as separate cards below this
                  toolbar card. The toolbar card only contains the
                  search / filter / group-by / view toggle; each batch
                  gets its own card so authorship (added by · when)
                  stays visually attached to its scenarios. */}
            </>
          )}
        </SectionCard>
      )}

      {/* v2 timeline: batches as chronological events on a vertical
          spine with a node per batch. v1 (BatchCardsList) is kept in
          this file for a one-line revert if v2 doesn't stick. */}
      {shown.length > 0 && (
        <BatchTimeline
          groups={shownGroups}
          view={view}
          env={env}
          envState={envState}
          buildMode={buildMode}
          selectedIds={selectedIds}
          onSelectionChange={handleSelectionChange}
          onEdit={setEditing}
          onRemove={removeScenario}
          onHideGroup={toggleGroupHidden}
          locked={locked}
        />
      )}

      {/*
        Gate-rejects panel intentionally suppressed: the table shows
        the full scenario list directly, so a "10 drafted · 5 kept ·
        5 rejected" filter below it just reads as if we're hiding
        rows. Kept the import around in case we want to bring it
        back behind a flag later.
      */}

      {/* Coverage now lives at the top of the tab (collapsed by
          default) so users see the three summary numbers on landing
          instead of having to scroll past the whole list. */}

      <AddScenariosDrawer
        open={adding}
        onClose={() => setAdding(false)}
        env={genEnv}
        envState={envState}
        selected={selected}
        onAdd={addScenarios}
      />
      <ScenarioEditor
        open={!!editing}
        onClose={() => setEditing(null)}
        row={editing}
        env={env}
        envState={envState}
        onSave={saveScenario}
        onBuilderPrompt={onBuilderPrompt}
      />
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  buildMode: PropTypes.bool,
  onBuilderPrompt: PropTypes.func,
  locked: PropTypes.bool,
  onFork: PropTypes.func,
};


/**
 * Shown when the environment has no scenarios at all.
 *
 * Rare — they are normally derived from the agent — but a blank half-screen
 * makes the step look broken rather than empty, so this shows what the step
 * produces: the shape of a scenario, built from this environment's own derived
 * rows rather than invented ones.
 */
function RoutePlaceholder({ env, onAdd }) {
  const sample = generatedPool(env).slice(0, 3);

  return (
    <Box sx={{ py: 5, px: 2, textAlign: "center" }}>
      <PickRouteIllustration />
      <Typography sx={{ typography: "m2", fontWeight: 600, mt: 2 }}>
        No scenarios yet
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.subtitle", mt: 0.5 }}>
        Whichever route you add from, you end up with a list of tasks like this.
      </Typography>
      <Button
        variant="contained"
        color="primary"
        size="small"
        onClick={onAdd}
        startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
        sx={{ typography: "s2", fontWeight: 700, mt: 2 }}
      >
        Add scenarios
      </Button>

      <Box
        sx={{
          maxWidth: 640, mx: "auto", mt: 3, textAlign: "left",
          border: "1px solid", borderColor: "divider", borderRadius: 1.5,
          overflow: "hidden", bgcolor: "background.paper",
        }}
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {sample.map((r) => (
            <Stack key={r.id} direction="row" alignItems="center" spacing={2} sx={{ px: 2, py: 1.25 }}>
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.title}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{r.task}</Typography>
              </Box>
              <Box sx={{ width: 180, flexShrink: 0, display: { xs: "none", md: "block" } }}>
                <PersonaBadge persona={r.persona} compact />
              </Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                ~{r.turns}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>

      <Typography sx={{ typography: "s3", color: "text.subtitle", fontStyle: "italic", mt: 1.5 }}>
        Sample preview — add scenarios to build your own.
      </Typography>
    </Box>
  );
}
RoutePlaceholder.propTypes = { env: PropTypes.object.isRequired, onAdd: PropTypes.func };

export function ScenarioRow({ row, index, onRemove, selectable, checked, onToggle }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        px: 2.5, py: 1.5,
        cursor: selectable ? "pointer" : "default",
        "&:hover": selectable ? { bgcolor: "action.hover" } : {},
      }}
      onClick={selectable ? onToggle : undefined}
    >
      {selectable ? (
        <Iconify
          icon={checked ? "solar:check-square-bold" : "solar:stop-linear"}
          width={18}
          sx={{ color: checked ? "primary.main" : "text.subtitle", flexShrink: 0 }}
        />
      ) : (
        <Typography sx={{ typography: "s3", color: "text.subtitle", width: 20, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
          {index + 1}
        </Typography>
      )}

      <Box sx={{ flex: 1.4, minWidth: 0 }}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{row.title}</Typography>
          {row.critical && (
            <Tooltip title="Critical — a failure here is a release blocker" arrow>
              <Box sx={{ display: "flex" }}>
                <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626" }} />
              </Box>
            </Tooltip>
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{row.task}</Typography>
      </Box>

      {/* Wide enough for a full job title — roles were truncating at 180. */}
      <Box sx={{ width: 240, flexShrink: 0, display: { xs: "none", md: "block" } }}>
        <PersonaBadge persona={row.persona} compact />
      </Box>

      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ width: 62, flexShrink: 0, display: { xs: "none", sm: "flex" } }}>
        <Iconify icon="solar:chat-round-line-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>~{row.turns}</Typography>
      </Stack>

      {onRemove && (
        <IconButton size="small" onClick={(e) => { e.stopPropagation(); onRemove(); }}>
          <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
        </IconButton>
      )}
    </Stack>
  );
}
ScenarioRow.propTypes = {
  row: PropTypes.object, index: PropTypes.number, onRemove: PropTypes.func,
  selectable: PropTypes.bool, checked: PropTypes.bool, onToggle: PropTypes.func,
};

/**
 * Provenance form on a scenario row — the visual mark that PRD §6.1.2
 * mandates. Three variants:
 *   - "user"      → filled amber square  ("you started this")
 *   - "auto"      → hollow ring           ("we started this")
 *   - "assistant" → filled green dot     ("the assistant did this")
 * Hover tooltip carries the full attribution.
 */
export function ProvenanceGlyph({ scenario, size = 10 }) {
  const s = ensureProvenance(scenario);
  const src = sourceOf(s.source);
  const label = provenanceLabel(s);
  return (
    <Tooltip title={`${src.label} · ${label}`} arrow>
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", width: 14, height: 14, flexShrink: 0 }}>
        {src.formKind === "user" && (
          <Box sx={{
            width: size, height: size, borderRadius: 0.375,
            bgcolor: "#F59E0B",
          }} />
        )}
        {src.formKind === "auto" && (
          <Box sx={{
            width: size, height: size, borderRadius: "50%",
            border: "1.5px solid",
            borderColor: (t) => alpha(t.palette.text.primary, 0.35),
          }} />
        )}
        {src.formKind === "assistant" && (
          <Box sx={{
            width: size, height: size, borderRadius: "50%",
            bgcolor: "#16A34A",
          }} />
        )}
      </Box>
    </Tooltip>
  );
}
ProvenanceGlyph.propTypes = { scenario: PropTypes.object, size: PropTypes.number };

/* ── grouping ────────────────────────────────────────────────────────────── */

/**
 * Use-case-based grouping.
 *
 * Kind buckets (Tool use / Rule enforcement / Data traps / …) group by
 * how the scenario was *derived*, which is a builder concept. A reader
 * scanning 30+ scenarios cares about *what task the agent is being
 * asked to do*: verify identity, look up an order, refuse a refund,
 * handle a saved-card record. Those are the real use cases and they
 * cut across the derivation kinds.
 *
 * The scenario's title carries this cleanly:
 *   Core   → "Routine task using {tool_name}"  → group by the tool
 *   Rule   → the rule text (short, one per rule)
 *   Trap   → "{table_name}: {note}"            → group by the table
 *   Adv    → the template title (Instruction override, Authority claim…)
 *   Edge   → the template title
 *
 * So every group's label is what the scenarios in it are actually
 * doing, not the builder bucket they fell out of.
 */
const humanize = (s = "") => s
  .replace(/[_-]/g, " ")
  .replace(/\b\w/g, (c) => c.toUpperCase())
  .trim();

const deriveUseCase = (row) => {
  /*
    The scenario now carries its own use-case sentence — the mock stamps
    `row.useCase` with a spec-line description of what the group tests
    ("Verify a caller's identity before touching account state"). Group
    key is a slug of that sentence so scenarios with matching text land
    in the same group. Legacy scenarios that predate the field fall back
    to the previous id-based derivation.
  */
  if (row?.useCase) {
    const slug = row.useCase.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return { id: slug, label: row.useCase };
  }
  const id = row?.id || "";
  const title = row?.title || "";
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return { id: `tool:${m[1].toLowerCase()}`, label: humanize(m[1]) };
    return { id: "tool:other", label: "Tool use" };
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 42).trim();
    return { id: `rule:${short.toLowerCase()}`, label: short || "Rule enforcement" };
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table) return { id: `trap:${table.toLowerCase()}`, label: `${humanize(table)} data` };
    return { id: "trap:other", label: "Data traps" };
  }
  if (id.includes("-adversarial-")) return { id: `adv:${title.toLowerCase()}`, label: title || "Adversarial" };
  if (id.includes("-edge-")) return { id: `edge:${title.toLowerCase()}`, label: title || "Edge case" };
  return { id: "other", label: "Other" };
};

/**
 * Group key + label for one row, in a given mode.
 *
 * The trace table on the run view uses the same three axes ("Goal",
 * "Persona", "Failure sub-goal"), so we mirror them here — same
 * derivation, so a scenarios-tab "Persona" bucket matches the trace
 * table's "Persona" bucket after a run.
 */
const groupKeyOf = (row, mode, env) => {
  if (mode === "persona") {
    const name = row?.persona?.name;
    if (!name) return { id: "persona:none", label: "No persona" };
    return { id: `persona:${name.toLowerCase()}`, label: name };
  }
  if (mode === "subgoal") {
    const subs = subTasksFor(row, env);
    const first = subs?.[0]?.label;
    if (!first) return { id: "subgoal:none", label: "No sub-goals" };
    const slug = first.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return { id: `subgoal:${slug}`, label: first };
  }
  if (mode === "source") {
    const s = ensureProvenance(row);
    const src = sourceOf(s.source);
    return { id: `source:${src.id}`, label: src.label };
  }
  if (mode === "addedBy") {
    const s = ensureProvenance(row);
    const who = s.addedBy?.name || "System";
    return { id: `addedBy:${who.toLowerCase()}`, label: `Added by ${who}` };
  }
  if (mode === "batch") {
    const s = ensureProvenance(row);
    const src = sourceOf(s.source);
    const when = new Date(s.addedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return { id: `batch:${s.batchId}`, label: `${src.label} · ${when}` };
  }
  return deriveUseCase(row);
};

/* Batches are ALWAYS the outer grouping — this dropdown selects the
   INNER grouping shown within each batch. "Batch" itself is not an
   option here because everything is batched by default. */
export const SCENARIO_GROUPINGS = [
  { id: "goal",    label: "Goal",     icon: "solar:target-linear" },
  { id: "persona", label: "Persona",  icon: "solar:user-rounded-linear" },
  { id: "subgoal", label: "Sub-goal", icon: "solar:map-linear" },
  { id: "source",  label: "Source",   icon: "solar:magic-stick-3-linear" },
  { id: "addedBy", label: "Added by", icon: "solar:user-plus-linear" },
];

const groupScenarios = (rows, mode = "goal", env) => {
  /* Two-level grouping (per user directive 2026-09-22): batches are
     always the outer container carrying who added them + when. The
     inner grouping is the current `mode` (Goal / Persona / Sub-goal /
     Source / Added by). We flatten to a single sorted list of groups,
     each tagged with its `batchMeta`, and let the renderers insert a
     batch-header row on batch transitions — that avoids rewriting the
     whole table/list to a nested-structure API. */
  const batches = groupByBatch(rows);
  const out = [];
  batches.forEach((batch) => {
    const buckets = new Map();
    batch.scenarios.forEach((r) => {
      const key = groupKeyOf(r, mode, env);
      if (!buckets.has(key.id)) buckets.set(key.id, { id: `${batch.batchId}__${key.id}`, label: key.label, rows: [], batchMeta: batch });
      buckets.get(key.id).rows.push(r);
    });
    /* Within a batch, bigger inner groups first. */
    [...buckets.values()]
      .sort((a, b) => b.rows.length - a.rows.length)
      .forEach((g) => out.push(g));
  });
  return out;
};

/**
 * Batches as separate cards. Takes the flat list of inner groups
 * (produced by groupScenarios), bundles them back by batch, and
 * renders one card per batch with a prominent authorship header.
 *
 * Per user directive (2026-09-22): "Show each batches separately …
 * where is the added by / added at details for the batch?" Batches
 * are the outer container; scenarios within a batch use the inner
 * group-by (goal / persona / etc.).
 */
function BatchCardsList({ groups, view, env, envState, buildMode, selectedIds, onSelectionChange, onEdit, onRemove, onHideGroup, locked }) {
  /* Rebundle flat groups into batches so each batch gets its own
     card. Groups arrive already sorted (batch → inner group) from
     groupScenarios, so a simple sweep is enough. */
  const batches = useMemo(() => {
    const out = [];
    let current = null;
    groups.forEach((g) => {
      const meta = g.batchMeta;
      if (!meta) return;
      if (!current || current.meta.batchId !== meta.batchId) {
        current = { meta, innerGroups: [], rows: [] };
        out.push(current);
      }
      current.innerGroups.push(g);
      current.rows.push(...g.rows);
    });
    return out;
  }, [groups]);

  if (batches.length === 0) return null;

  return (
    <Stack spacing={3}>
      {batches.map((b) => (
        <BatchCard
          key={b.meta.batchId}
          batch={b.meta}
          rowCount={b.rows.length}
          innerGroups={b.innerGroups}
          view={view}
          env={env}
          envState={envState}
          buildMode={buildMode}
          selectedIds={selectedIds}
          onSelectionChange={onSelectionChange}
          onEdit={onEdit}
          onRemove={onRemove}
          onHideGroup={onHideGroup}
          locked={locked}
        />
      ))}
    </Stack>
  );
}
BatchCardsList.propTypes = {
  groups: PropTypes.array, view: PropTypes.string,
  env: PropTypes.object, envState: PropTypes.object, buildMode: PropTypes.bool,
  selectedIds: PropTypes.array, onSelectionChange: PropTypes.func,
  onEdit: PropTypes.func, onRemove: PropTypes.func, onHideGroup: PropTypes.func,
  locked: PropTypes.bool,
};

function BatchCard({ batch, rowCount, innerGroups, view, env, envState, buildMode, selectedIds, onSelectionChange, onEdit, onRemove, onHideGroup, locked }) {
  const [open, setOpen] = useState(true);
  const src = sourceOf(batch.source);
  const initials = (batch.addedBy?.name || "System").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return (
    <SectionCard sx={{ mb: 0 }}>
      {/* Batch header: prominent authorship info + collapsible chevron */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        onClick={() => setOpen((v) => !v)}
        sx={{
          px: 2.5, py: 2, cursor: "pointer",
          borderBottom: open ? "1px solid" : "none",
          borderColor: "divider",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Box sx={{
          width: 36, height: 36, borderRadius: "50%",
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.06),
          color: "text.primary", fontSize: 12, fontWeight: 700, letterSpacing: 0.4,
          flexShrink: 0,
        }}>
          {batch.addedBy?.kind === "user" ? initials : (
            <Iconify icon={src.icon} width={16} />
          )}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s1", fontWeight: 700, fontSize: 14 }}>
              Added by {batch.addedBy?.name || "System"}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
              · {relativeTime(batch.addedAt)}
            </Typography>
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 0.375 }}>
            <Iconify icon={src.icon} width={11} sx={{ color: "text.subtitle" }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5 }}>
              {src.label} · {new Date(batch.addedAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            </Typography>
          </Stack>
        </Box>
        <Typography sx={{
          typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums",
          color: "text.primary", fontSize: 13, flexShrink: 0,
        }}>
          {rowCount} {rowCount === 1 ? "scenario" : "scenarios"}
        </Typography>
        <Iconify
          icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
          width={16} sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      </Stack>
      {open && (
        view === "table" ? (
          <ScenarioTable
            rows={innerGroups.flatMap((g) => g.rows)}
            groups={innerGroups}
            env={env}
            onEdit={onEdit}
            onRemove={onRemove}
            onHideGroup={onHideGroup}
            selectedIds={selectedIds}
            onSelectionChange={onSelectionChange}
            locked={locked}
          />
        ) : (
          <GroupedScenarioList
            groups={innerGroups}
            env={env}
            envState={envState}
            buildMode={buildMode}
            onEdit={onEdit}
            onRemove={onRemove}
            onHideGroup={onHideGroup}
            selectedIds={selectedIds}
            onSelectionChange={onSelectionChange}
            locked={locked}
          />
        )
      )}
    </SectionCard>
  );
}
BatchCard.propTypes = {
  batch: PropTypes.object, rowCount: PropTypes.number, innerGroups: PropTypes.array,
  view: PropTypes.string, env: PropTypes.object, envState: PropTypes.object, buildMode: PropTypes.bool,
  selectedIds: PropTypes.array, onSelectionChange: PropTypes.func,
  onEdit: PropTypes.func, onRemove: PropTypes.func, onHideGroup: PropTypes.func,
  locked: PropTypes.bool,
};

/* ── v2 · timeline view ────────────────────────────────────────────
   An alternative to BatchCardsList (v1). Batches read as events on a
   vertical spine — like a git-log or activity feed. Each batch node
   carries author, time, source label and count; expanded, its
   scenarios sit indented to the right of the spine under the current
   inner group-by. Kept in the same file so swapping v1 ↔ v2 is a
   one-line edit at the callsite. */

function BatchTimeline({ groups, view, env, envState, buildMode, selectedIds, onSelectionChange, onEdit, onRemove, onHideGroup, locked }) {
  const batches = useMemo(() => {
    const out = [];
    let current = null;
    groups.forEach((g) => {
      const meta = g.batchMeta;
      if (!meta) return;
      if (!current || current.meta.batchId !== meta.batchId) {
        current = { meta, innerGroups: [], rows: [] };
        out.push(current);
      }
      current.innerGroups.push(g);
      current.rows.push(...g.rows);
    });
    return out;
  }, [groups]);

  if (batches.length === 0) return null;

  return (
    <Box sx={{ position: "relative", pl: 3.5, pr: 0.5 }}>
      {/* Vertical spine down the left. Tucked behind the nodes so the
          nodes appear to sit on the line. */}
      <Box sx={{
        position: "absolute", left: 15, top: 12, bottom: 12, width: "2px",
        bgcolor: "divider",
        borderRadius: 1,
      }} />
      <Stack spacing={4}>
        {batches.map((b, i) => (
          <TimelineNode
            key={b.meta.batchId}
            batch={b.meta}
            rowCount={b.rows.length}
            innerGroups={b.innerGroups}
            view={view}
            env={env}
            envState={envState}
            buildMode={buildMode}
            selectedIds={selectedIds}
            onSelectionChange={onSelectionChange}
            onEdit={onEdit}
            onRemove={onRemove}
            onHideGroup={onHideGroup}
            locked={locked}
            defaultOpen={i === 0}
          />
        ))}
      </Stack>
    </Box>
  );
}
BatchTimeline.propTypes = {
  groups: PropTypes.array, view: PropTypes.string,
  env: PropTypes.object, envState: PropTypes.object, buildMode: PropTypes.bool,
  selectedIds: PropTypes.array, onSelectionChange: PropTypes.func,
  onEdit: PropTypes.func, onRemove: PropTypes.func, onHideGroup: PropTypes.func,
  locked: PropTypes.bool,
};

function TimelineNode({ batch, rowCount, innerGroups, view, env, envState, buildMode, selectedIds, onSelectionChange, onEdit, onRemove, onHideGroup, locked, defaultOpen }) {
  const [open, setOpen] = useState(!!defaultOpen);
  const src = sourceOf(batch.source);
  const initials = (batch.addedBy?.name || "System").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  /* First few scenario names to show inline under the header — makes
     it obvious there IS content in this batch, so the "Show N" button
     doesn't have to carry the whole discovery load. */
  const previewNames = innerGroups.flatMap((g) => g.rows).slice(0, 3).map((r) => r.name || r.title || r.id);
  return (
    <Box sx={{ position: "relative" }}>
      <Box sx={{
        position: "absolute", left: -22, top: 8,
        width: 20, height: 20, borderRadius: "50%",
        display: "flex", alignItems: "center", justifyContent: "center",
        bgcolor: "background.paper",
        border: "2px solid",
        borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.35 : 0.25),
      }}>
        <Box sx={{
          width: 8, height: 8, borderRadius: "50%",
          bgcolor: "text.primary",
        }} />
      </Box>

      {/* Batch header. Whole row is clickable so the affordance is
          redundant with the explicit Show/Hide button on the right —
          per user callout that a tiny chevron was too easy to miss. */}
      <Stack
        direction="row" alignItems="flex-start" spacing={1.5}
        onClick={() => setOpen((v) => !v)}
        sx={{
          px: 1.5, py: 1.25, borderRadius: 1.25, cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Box sx={{
          width: 28, height: 28, borderRadius: "50%",
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.08),
          color: "text.primary", fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4,
          flexShrink: 0,
        }}>
          {initials}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
            <Typography sx={{ typography: "s2", fontWeight: 700, fontSize: 13.5 }}>
              {batch.addedBy?.name || "System"}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
              added {rowCount} {rowCount === 1 ? "scenario" : "scenarios"}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 12 }}>
              · via {src.label.toLowerCase()}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11, mt: 0.25 }}>
            {relativeTime(batch.addedAt)} · {new Date(batch.addedAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
          </Typography>
          {/* Preview chips of the first few scenario names — hidden
              when expanded (the full list carries the same content). */}
          {!open && previewNames.length > 0 && (
            <Stack direction="row" spacing={0.75} sx={{ mt: 1, flexWrap: "wrap", rowGap: 0.75 }}>
              {previewNames.map((n) => (
                <Box key={n} sx={{
                  px: 1, py: 0.375, borderRadius: 0.75,
                  border: "1px solid", borderColor: "divider",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02),
                }}>
                  <Typography noWrap sx={{
                    typography: "s3", fontSize: 11, color: "text.subtitle",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                    maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
                  }} title={n}>
                    {n}
                  </Typography>
                </Box>
              ))}
              {rowCount > previewNames.length && (
                <Typography sx={{
                  typography: "s3", fontSize: 11, color: "text.subtitle",
                  alignSelf: "center", ml: 0.25,
                }}>
                  +{rowCount - previewNames.length} more
                </Typography>
              )}
            </Stack>
          )}
        </Box>
        {/* Explicit expand affordance — button-styled with border so it
            reads as an action, not a static count. Whole row is still
            clickable for a big hit area. */}
        <Stack
          direction="row" alignItems="center" spacing={0.75}
          onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
          sx={{
            px: 1.25, py: 0.5, borderRadius: 0.875,
            border: "1px solid", borderColor: "divider",
            bgcolor: "background.paper",
            cursor: "pointer", flexShrink: 0,
            transition: "border-color 120ms, background-color 120ms",
            "&:hover": {
              borderColor: (t) => alpha(t.palette.text.primary, 0.4),
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
            },
          }}
        >
          <Typography sx={{
            typography: "s2", fontWeight: 600, fontSize: 12,
            color: "text.primary", whiteSpace: "nowrap",
          }}>
            {open ? `Hide ${rowCount}` : `Show ${rowCount} scenarios`}
          </Typography>
          <Iconify
            icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
            width={13} sx={{ color: "text.subtitle" }}
          />
        </Stack>
      </Stack>

      {/* Expanded batch body — sits indented under the node, still
          right of the spine. Rendered as a lightly bordered surface
          so it reads as "this batch's payload" without competing with
          the batch header. */}
      {open && (
        <Box sx={{
          mt: 1, ml: 0.5,
          border: "1px solid", borderColor: "divider", borderRadius: 1.5,
          bgcolor: "background.paper",
          overflow: "hidden",
        }}>
          {view === "table" ? (
            <ScenarioTable
              rows={innerGroups.flatMap((g) => g.rows)}
              groups={innerGroups}
              env={env}
              onEdit={onEdit}
              onRemove={onRemove}
              onHideGroup={onHideGroup}
              selectedIds={selectedIds}
              onSelectionChange={onSelectionChange}
              locked={locked}
            />
          ) : (
            <GroupedScenarioList
              groups={innerGroups}
              env={env}
              envState={envState}
              buildMode={buildMode}
              onEdit={onEdit}
              onRemove={onRemove}
              onHideGroup={onHideGroup}
              selectedIds={selectedIds}
              onSelectionChange={onSelectionChange}
              locked={locked}
            />
          )}
        </Box>
      )}
    </Box>
  );
}
TimelineNode.propTypes = {
  batch: PropTypes.object, rowCount: PropTypes.number, innerGroups: PropTypes.array,
  view: PropTypes.string, env: PropTypes.object, envState: PropTypes.object, buildMode: PropTypes.bool,
  selectedIds: PropTypes.array, onSelectionChange: PropTypes.func,
  onEdit: PropTypes.func, onRemove: PropTypes.func, onHideGroup: PropTypes.func,
  locked: PropTypes.bool, defaultOpen: PropTypes.bool,
};

/**
 * Just the grouped body — search, filter and view tabs live at the
 * page level now so the same toolbar drives both list and table
 * views. This renders each use-case section as a collapsible block
 * with a sticky header.
 */
function GroupedScenarioList({ groups, env, envState, buildMode, onEdit, onRemove, onHideGroup, selectedIds = [], onSelectionChange, locked = false }) {
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const toggleRow = (id) => {
    if (!onSelectionChange) return;
    onSelectionChange(selectedSet.has(id)
      ? selectedIds.filter((v) => v !== id)
      : [...selectedIds, id]);
  };
  const toggleGroup = (groupRows, allSelected) => {
    if (!onSelectionChange) return;
    const ids = groupRows.map((r) => r.id);
    onSelectionChange(allSelected
      ? selectedIds.filter((id) => !ids.includes(id))
      : [...new Set([...selectedIds, ...ids])]);
  };
  return (
    <Box>
      {groups.map((g) => (
        <CollapsibleGroup
          key={g.id}
          group={g}
          env={env}
          envState={envState}
          buildMode={buildMode}
          onEdit={onEdit}
          onRemove={onRemove}
          onHideGroup={onHideGroup}
          selectedSet={selectedSet}
          onToggleRow={toggleRow}
          onToggleGroup={toggleGroup}
          selectable={!!onSelectionChange && !locked}
          locked={locked}
        />
      ))}
    </Box>
  );
}
GroupedScenarioList.propTypes = {
  groups: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  buildMode: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedIds: PropTypes.array,
  onSelectionChange: PropTypes.func,
};

/**
 * One collapsible group section. Header stays sticky when expanded so
 * scanning a big group keeps the current use case pinned at the top.
 * Chevron flips right → down on toggle. Header row is the whole click
 * target so there's no tiny hit area.
 */
function CollapsibleGroup({ group, env, envState, buildMode, onEdit, onRemove, onHideGroup, selectedSet, onToggleRow, onToggleGroup, selectable, locked = false }) {
  const [open, setOpen] = useState(true);

  const selectedInGroup = group.rows.filter((r) => selectedSet?.has(r.id)).length;
  const allSelected = selectable && selectedInGroup === group.rows.length && group.rows.length > 0;
  const someSelected = selectable && selectedInGroup > 0 && !allSelected;

  return (
    <Box>
      {/*
        Group header is a distinctive strip — neutral background, larger
        label typography, count as a pill — so the eye can tell at a
        glance "this is a use case group" versus "this is a scenario
        inside it". Before this both used the same s2/700/primary and
        the two levels blended into one long list.
      */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        onClick={() => setOpen((o) => !o)}
        sx={{
          position: "sticky", top: 0, zIndex: 2, cursor: "pointer",
          px: 2.5, py: 1.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid", borderColor: "divider",
          borderTop: "1px solid", borderTopColor: "divider",
          "&:hover": {
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
          },
        }}
      >
        {selectable && (
          <Checkbox
            size="small"
            checked={allSelected}
            indeterminate={someSelected}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggleGroup(group.rows, allSelected)}
            sx={{
              p: 0, flexShrink: 0,
              color: "text.disabled",
              "&.Mui-checked": { color: "text.primary" },
              "&.MuiCheckbox-indeterminate": { color: "text.primary" },
            }}
          />
        )}
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={15}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          sx={{
            typography: "s1", fontWeight: 700, color: "text.primary",
            flex: 1, minWidth: 0,
          }}
        >
          {group.label}
        </Typography>
        <Typography
          sx={{
            px: 1, py: 0.25, borderRadius: 0.75,
            typography: "s3", fontWeight: 700, color: "text.secondary",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.06),
            fontVariantNumeric: "tabular-nums", flexShrink: 0,
            letterSpacing: 0.2,
          }}
        >
          {group.rows.length} {group.rows.length === 1 ? "scenario" : "scenarios"}
        </Typography>
        {onHideGroup && (
          <Tooltip arrow title="Hide this group">
            <IconButton
              size="small"
              onClick={(e) => { e.stopPropagation(); onHideGroup(group.id); }}
              sx={{ flexShrink: 0, color: "text.subtitle", "&:hover": { color: "text.primary" } }}
            >
              <Iconify icon="solar:eye-closed-linear" width={15} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>

      {open && (
        /*
          Scenario rows are indented and share a subtle left rail so
          they visibly nest inside the group above. The rail is the
          single strongest signal that "these all belong together
          under the header you just read".
        */
        <Box sx={{ pl: 3 }}>
          <Stack
            divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
            sx={{
              borderLeft: "2px solid",
              borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
            }}
          >
            {group.rows.map((s) => {
              const checked = !!selectedSet?.has(s.id);
              return (
                <Stack key={s.id} direction="row" alignItems="flex-start">
                  {selectable && (
                    <Box sx={{ pt: 1.75, pl: 1.75, flexShrink: 0 }}>
                      <Checkbox
                        size="small"
                        checked={checked}
                        onChange={() => onToggleRow(s.id)}
                        sx={{
                          p: 0,
                          color: "text.disabled",
                          "&.Mui-checked": { color: "text.primary" },
                        }}
                      />
                    </Box>
                  )}
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <ScenarioDetail row={s} env={env} envState={envState} buildMode={buildMode} />
                  </Box>
                  <Tooltip arrow title={locked ? "Fork this environment to edit." : "Edit scenario"}>
                    <span>
                      <IconButton size="small" disabled={locked} onClick={() => onEdit(s)} sx={{ mt: 1, flexShrink: 0 }}>
                        <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                      </IconButton>
                    </span>
                  </Tooltip>
                  <Tooltip arrow title={locked ? "Fork this environment to edit." : "Delete scenario"}>
                    <span>
                      <IconButton size="small" disabled={locked} onClick={() => onRemove(s.id)} sx={{ mt: 1, mr: 1.5, flexShrink: 0 }}>
                        <Iconify icon="solar:trash-bin-trash-linear" width={16} sx={{ color: "text.subtitle" }} />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Stack>
              );
            })}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
CollapsibleGroup.propTypes = {
  group: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  buildMode: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedSet: PropTypes.object,
  onToggleRow: PropTypes.func,
  onToggleGroup: PropTypes.func,
  selectable: PropTypes.bool,
};

/* ── filter popover ──────────────────────────────────────────────────────── */

/**
 * Use-case filter popover.
 *
 * The old MUI Menu clipped long use-case sentences off the right edge
 * and read as one dense list. This is a small custom popover instead:
 * fixed width, wrapping labels, an inline search for long lists, and
 * a clear header + footer treatment so the frame reads as a real
 * filter panel rather than a menu.
 */
/**
 * Multi-dimension scenario filter menu — same MUI Menu + section-heading +
 * MenuItem shape the Improvements list uses. Sections: Status, Persona,
 * Kind, Sub-goal. Multi-select: a check mark on the right of each row
 * indicates selection; clicking a row toggles it without closing the menu.
 */
function ScenarioFilterMenu({
  anchorEl, onClose, filters, onToggle, onClearAll,
  scenarios, statusOf, personaOf, kindOf, subgoalOf,
}) {
  const STATUS_OPTIONS = [
    { id: "broken",   label: "Broken" },
    { id: "critical", label: "Critical" },
    { id: "edited",   label: "Edited" },
  ];
  const KIND_OPTIONS = [
    { id: "happy",       label: "Happy path" },
    { id: "rule",        label: "Rule enforcement" },
    { id: "trap",        label: "Data trap" },
    { id: "adversarial", label: "Adversarial" },
    { id: "edge",        label: "Edge case" },
  ];

  const counts = (getter, options) => {
    const c = {};
    scenarios.forEach((r) => {
      const val = getter(r);
      const list = Array.isArray(val) ? val : [val];
      list.forEach((v) => { if (v == null) return; c[v] = (c[v] || 0) + 1; });
    });
    return options.map((o) => ({ ...o, count: c[o.id] || 0 }));
  };
  const dynamicOptions = (getter) => {
    const c = new Map();
    scenarios.forEach((r) => {
      const v = getter(r);
      if (!v) return;
      c.set(v, (c.get(v) || 0) + 1);
    });
    return [...c.entries()].map(([id, count]) => ({ id, label: id, count }))
      .sort((a, b) => b.count - a.count);
  };

  const totalSelected = filters.status.length + filters.persona.length + filters.kind.length + filters.subgoal.length;

  const renderSection = (title, dim, options, isFirst) => {
    if (!options.length) return null;
    return (
      <Box key={title}>
        <Stack
          direction="row" alignItems="center"
          sx={{ px: 1, pt: isFirst ? 0 : 1, pb: 0.5 }}
        >
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, flex: 1 }}>
            {title}
          </Typography>
        </Stack>
        {options.map((o) => {
          const isSelected = filters[dim].includes(o.id);
          return (
            <MenuItem
              key={o.id}
              selected={isSelected}
              onClick={() => onToggle(dim, o.id)}
              sx={{ typography: "s2", pr: 1.25 }}
            >
              <Box sx={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {o.label}
              </Box>
              <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", mr: isSelected ? 0.75 : 2.25 }}>
                {o.count}
              </Typography>
              {isSelected && (
                <ListItemIcon sx={{ minWidth: "auto !important", color: "primary.main" }}>
                  <Iconify icon="eva:checkmark-fill" width={16} />
                </ListItemIcon>
              )}
            </MenuItem>
          );
        })}
      </Box>
    );
  };

  return (
    <Menu
      anchorEl={anchorEl}
      open={!!anchorEl}
      onClose={onClose}
      slotProps={{ paper: { sx: { minWidth: 280, maxHeight: 520, p: 1.5 } } }}
    >
      {totalSelected > 0 && (
        <Stack direction="row" alignItems="center" sx={{ px: 1, pb: 0.75, mb: 0.5, borderBottom: "1px solid", borderColor: "divider" }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
            {totalSelected} selected
          </Typography>
          <Button size="small" onClick={onClearAll} sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}>
            Clear all
          </Button>
        </Stack>
      )}
      {renderSection("Status", "status", counts(statusOf, STATUS_OPTIONS), true)}
      {renderSection("Persona", "persona", dynamicOptions(personaOf))}
      {renderSection("Kind", "kind", counts(kindOf, KIND_OPTIONS))}
      {renderSection("Sub-goal", "subgoal", dynamicOptions(subgoalOf))}
    </Menu>
  );
}
ScenarioFilterMenu.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  filters: PropTypes.object,
  onToggle: PropTypes.func,
  onClearAll: PropTypes.func,
  scenarios: PropTypes.array,
  statusOf: PropTypes.func,
  personaOf: PropTypes.func,
  kindOf: PropTypes.func,
  subgoalOf: PropTypes.func,
};

function UseCaseFilterPopover({ anchorEl, onClose, allUseCases, countBy, selected, onChange, dimensionLabel = "use case" }) {
  const [q, setQ] = useState("");

  /* Reset the internal search when the popover closes so it opens
     fresh next time. */
  const handleClose = () => { setQ(""); onClose(); };

  const filtered = q.trim()
    ? allUseCases.filter((uc) => uc.label.toLowerCase().includes(q.trim().toLowerCase()))
    : allUseCases;

  const toggle = (id) => onChange(selected.includes(id)
    ? selected.filter((v) => v !== id)
    : [...selected, id]);

  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 420,
            mt: 0.75,
            borderRadius: 1.5,
            border: "1px solid",
            borderColor: "divider",
            boxShadow: (t) => t.customShadows?.dropdown || t.shadows[6],
            overflow: "hidden",
          },
        },
      }}
    >
      {/* header */}
      <Stack
        direction="row" alignItems="center"
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="mage:filter" width={14} sx={{ color: "text.secondary", mr: 0.75 }} />
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>
          {`Filter by ${dimensionLabel.toLowerCase()}`}
        </Typography>
        {selected.length > 0 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mr: 0.75 }}>
            {selected.length} selected
          </Typography>
        )}
      </Stack>

      {/* inline search — proper bordered input so it reads as a real
          field. The old borderless neutral pill looked like a
          placeholder that never rendered. Height and padding match
          the other TextFields in this flow. */}
      {allUseCases.length > 6 && (
        <Box sx={{ px: 1.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box
            sx={{
              display: "flex", alignItems: "center", gap: 0.75,
              px: 1.25, height: 34, borderRadius: 1,
              border: "1px solid", borderColor: "divider",
              bgcolor: "background.paper",
              transition: "border-color .12s ease",
              "&:focus-within": {
                borderColor: (t) => t.palette.mode === "dark"
                  ? alpha(t.palette.text.primary, 0.35)
                  : "#7857FC",
              },
            }}
          >
            <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <InputBase
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={`Search ${dimensionLabel.toLowerCase()}s…`}
              autoFocus
              sx={{ typography: "s2", flex: 1, color: "text.primary" }}
            />
            {q && (
              <IconButton size="small" onClick={() => setQ("")} sx={{ p: 0.25 }}>
                <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
              </IconButton>
            )}
          </Box>
        </Box>
      )}

      {/* list */}
      <Box sx={{ maxHeight: 340, overflowY: "auto", py: 0.5 }}>
        {filtered.length === 0 ? (
          <Box sx={{ px: 2, py: 4, textAlign: "center" }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              No use cases match &ldquo;{q}&rdquo;.
            </Typography>
          </Box>
        ) : (
          filtered.map((uc) => {
            const on = selected.includes(uc.id);
            const count = countBy(uc.id);
            return (
              <Stack
                key={uc.id}
                direction="row" alignItems="flex-start" spacing={1.25}
                onClick={() => toggle(uc.id)}
                sx={{
                  px: 2, py: 1, cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Checkbox
                  size="small" checked={on} disableRipple
                  sx={{
                    p: 0, mt: "1px", flexShrink: 0,
                    color: "text.disabled",
                    "&.Mui-checked": { color: "text.primary" },
                    "&.MuiCheckbox-indeterminate": { color: "text.primary" },
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2", color: "text.primary",
                    flex: 1, minWidth: 0,
                    /* wrap long use-case sentences instead of clipping
                       them off the right edge */
                    whiteSpace: "normal", lineHeight: 1.4,
                  }}
                >
                  {uc.label}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3", color: "text.subtitle",
                    flexShrink: 0, mt: "1px",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count}
                </Typography>
              </Stack>
            );
          })
        )}
      </Box>

      {/* footer */}
      <Stack
        direction="row" alignItems="center" justifyContent="space-between"
        sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small"
          onClick={() => onChange([])}
          disabled={selected.length === 0}
          sx={{
            typography: "s2", fontWeight: 600, textTransform: "none",
            color: selected.length ? "text.secondary" : "text.disabled",
            minWidth: 0, px: 0,
            "&:hover": { bgcolor: "transparent", color: "text.primary" },
          }}
        >
          Clear all
        </Button>
        <Button
          size="small" variant="contained"
          onClick={handleClose}
          sx={{
            typography: "s2", fontWeight: 700, textTransform: "none",
            /* White in both themes — matches the neutral chrome of the
               popover and reads as the primary action without pulling
               in the purple brand tone. */
            bgcolor: "common.white", color: "grey.900",
            boxShadow: "none",
            border: "1px solid",
            borderColor: (t) => alpha(t.palette.common.black, 0.08),
            "&:hover": {
              bgcolor: "common.white",
              boxShadow: "none",
              borderColor: (t) => alpha(t.palette.common.black, 0.2),
            },
          }}
        >
          Done
        </Button>
      </Stack>
    </Popover>
  );
}
UseCaseFilterPopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  allUseCases: PropTypes.array,
  countBy: PropTypes.func,
  selected: PropTypes.array,
  onChange: PropTypes.func,
};
