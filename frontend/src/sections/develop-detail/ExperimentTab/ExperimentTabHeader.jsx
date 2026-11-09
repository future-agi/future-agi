import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  IconButton,
  InputAdornment,
  Stack,
  Typography,
} from "@mui/material";
import { useLocation, useNavigate } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { ViewMenu, useColumnLayout } from "src/sections/common/ux/oneview";
import {
  fmtCount,
  fmtTimeAbsolute,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import { usePendingChangesGate } from "../DataTab/Versions/PendingChangesGate";
import { START_FROM_PRESET } from "../Experiment/StartFromExperiment";
import { useRunExperimentStore } from "../states";
import { headlineVerdict } from "./leaderboardHeadline";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";
import { useRunEvaluationStore } from "../states";
import SelectionBar from "./SelectionBar";
import { leaderboardGates } from "./leaderboardAccess";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import LeaderboardFilters from "./LeaderboardFilters";
import { useLeaderboardActions } from "./useLeaderboardActions";
import { useLeaderboardView } from "./useLeaderboard";
import {
  CHART_CARDS,
  LB_SURFACE,
  useLeaderboardPrefs,
} from "./leaderboardPrefs";
import { columnCatalog } from "./leaderboardColumns";
// HO results R7 (W5 B-H2): run metrics as leaderboard columns.
import { useLeaderboardRunMetrics } from "src/sections/common/DerivedMetrics/useLeaderboardRunMetrics";
import { compareSelection, compareUrl } from "./leaderboardModel";
import { pickHeadline } from "./leaderboardHeadline";
import useLeaderboardNumbers from "./useLeaderboardNumbers";
import {
  EveryRunSwitch,
  GroupBySelect,
  LeaderboardViewButton,
  TriggerButton,
} from "./LeaderboardToolbarMenus";
import useLeaderboardRoom from "./useLeaderboardRoom";
import { withRunFilter } from "src/sections/common/Selection/filterToSelected";

const CHART_SHORT = {
  scores: "Scores chart",
  latency: "Latency chart",
  cost: "Total cost chart",
  errors: "Error rate chart",
};

/**
 * EL#41: the baseline trigger's full story: run, what it ran, who pinned it
 * and when, and its dataset version when that is not the latest.
 */
function baselineTip(view) {
  const b = view.baseline;
  if (!b) return "";
  const run = view.baselineRun;
  const what = run ? [run.label, run.name].filter(Boolean).join(" ") : b.label;
  const how =
    b.source === "pinned"
      ? [
          "Pinned",
          b.pinned_by ? `by ${b.pinned_by}` : null,
          b.pinned_at
            ? `${fmtTimeRelative(b.pinned_at)}, ${fmtTimeAbsolute(b.pinned_at)}`
            : null,
        ]
          .filter(Boolean)
          .join(" ")
      : b.source === "url"
        ? "Baseline for this view only (from the link)"
        : "Auto: the earliest completed run";
  const latest = (view.versions ?? []).find((v) => v.is_latest)?.number;
  const ran = run?.dataset_version?.number;
  const version =
    latest != null && ran != null && ran !== latest
      ? `Ran on v${ran}, not the latest (v${latest})`
      : null;
  return [
    `Baseline: ${what}`,
    how,
    version,
    "Every delta on this page reads against it.",
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Experiments tab toolbar (§3.7, ONE-VIEW-SPEC A2-04): ONE row —
 * `Search experiments, runs, models  /` · `Filter` (badge; Version is its
 * first field, an active `v2 ×` chip after it) · `Group: Experiment ▾` ·
 * `Baseline #2B ▾` · spacer · `View` (columns, what the page shows, saved
 * views, Reset view) + `N hidden` · `Evals (15)` · `New experiment`. With
 * runs selected it becomes the selection bar. Keys: `/` search, `F` filter,
 * `Esc` clear selection, `C` compare.
 */
const ExperimentTabHeader = ({ tabRef, rowSelected = [], setRowSelected }) => {
  const view = useLeaderboardView();
  const navigate = useNavigate();
  const location = useLocation();
  const { role } = useAuthContext();
  const prefs = useLeaderboardPrefs(view.datasetId);
  const setOpenRunEvaluation = useRunEvaluationStore(
    (s) => s.setOpenRunEvaluation,
  );
  const searchRef = useRef(null);
  const [filtersOpen, setFiltersOpen] = useState(view.activeFilters.length > 0);
  const [search, setSearch] = useState(view.q);
  const debounced = useDebounce(search, 300);
  // W4 §9 (M6): the role and the dataset's access decide the doors.
  const access = useDatasetAccess(view.datasetId);
  const gates = useMemo(
    () =>
      leaderboardGates(
        {
          roleEdit: RolePermission.DATASETS[PERMISSIONS.UPDATE][role],
          roleDelete: RolePermission.DATASETS[PERMISSIONS.DELETE][role],
        },
        access,
      ),
    // access.can / access.reason are memoised on the access view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [role, access.can, access.reason],
  );
  const { canEdit, canRun, canDelete } = gates;
  const fromUrl = `${location.pathname}${location.search}`;

  const clearSelection = () => {
    tabRef?.current?.clearRowSelection?.();
    setRowSelected([]);
  };
  const actions = useLeaderboardActions(view.datasetId, {
    metricDefs: view.metricDefs,
    pinnedRunId: view.pinnedRunId,
    runs: view.allRuns,
    onDone: clearSelection,
  });

  useEffect(() => {
    if (debounced !== view.q) view.setQ(debounced);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);
  useEffect(() => {
    // Clear filters / back-forward change q from outside.
    if (view.q !== debounced) setSearch(view.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.q]);

  // Selected runs refreshed from the latest poll (status, progress).
  const selected = rowSelected
    .map((r) => view.allRuns.find((x) => x.run_id === r.run_id) ?? r)
    .filter(Boolean);

  const gate = usePendingChangesGate(view.datasetId);
  const openNew = (opts) =>
    gate("experiment", () =>
      useRunExperimentStore.getState().initiateCreateMode(opts),
    );

  // R4 (lane Rb, D26): `C` with nothing ticked opens latest vs previous
  // (else the best run with the baseline); with a selection, that.
  const numbers = useLeaderboardNumbers(view);
  const defaultPair = (() => {
    if (numbers.pair) return [numbers.pair.previous, numbers.pair.latest];
    const top = numbers.best?.id
      ? view.allRuns.find((r) => r.run_id === numbers.best.id)
      : null;
    return top && view.baselineRun && top.run_id !== view.baselineRun.run_id
      ? [view.baselineRun, top]
      : null;
  })();
  const compareSelected = () => {
    if (!selected.length) {
      if (!defaultPair) return;
      navigate(
        compareUrl(view.datasetId, {
          runIds: defaultPair.map((r) => r.run_id),
          baselineId: defaultPair[0].run_id,
          candId: defaultPair[1].run_id,
          runs: defaultPair,
          from: fromUrl,
        }),
      );
      return;
    }
    const c = compareSelection(selected, view.baseline?.run_id, {
      pinnedId: view.pinnedRunId,
      candId: pickHeadline(view.allRuns, view.baselineRun)?.candidate?.run_id,
    });
    if (c.error) return;
    navigate(compareUrl(view.datasetId, { ...c, from: fromUrl }));
  };
  // EL#28 / F7: one binding for the surface (the grid moves through the
  // tab's ref); `?` and `/` never type into the search field.
  const kb = () => tabRef?.current?.kb;
  useShortcuts("leaderboard", {
    // The empty tab has no toolbar search: `/` reaches the six cards' one.
    search: () =>
      (
        searchRef.current?.querySelector("input") ??
        document.querySelector('[data-testid="source-search"]')
      )?.focus(),
    filter: () => setFiltersOpen((o) => !o),
    compareSelected,
    next: () => kb()?.move(1),
    prev: () => kb()?.move(-1),
    select: () => kb()?.toggleSelect(),
    open: () => kb()?.open(),
    makeBaseline: () => kb()?.pin(),
    rerunWithChanges: () => kb()?.rerunChanges(),
    toggleGroup: () => kb()?.toggleGroup(),
  });
  useShortcuts(
    "global",
    { escape: selected.length ? clearSelection : undefined },
    { register: false },
  );

  // The same layout the grid shapes (G's store keeps them in step); the
  // grid's instance registers ⌘K `Columns…`.
  const room = useLeaderboardRoom();
  const runMetrics = useLeaderboardRunMetrics(view.datasetId, view.allRuns);
  const catalog = useMemo(
    () => columnCatalog(view.metricDefs, view.allRuns, { room, runMetrics }),
    [view.metricDefs, view.allRuns, room, runMetrics],
  );
  const layout = useColumnLayout({
    surface: LB_SURFACE,
    scopeId: view.datasetId,
    columns: catalog,
    commands: false,
  });
  const evalCount = view.metricDefs.filter((d) => d.is_dataset_eval).length;
  const noThreshold = view.metricDefs.filter(
    (d) =>
      d.is_dataset_eval &&
      d.output_type === "score" &&
      d.pass_threshold == null,
  ).length;
  const loaded = !!view.query.data;
  // Empty dataset: the body offers New experiment / Run from code; the
  // view tools have nothing to act on yet.
  const isEmpty = loaded && !view.allRuns.length && view.version === "all";
  const showTools = loaded && !isEmpty;
  const enoughRuns = view.runs.length >= 2;
  const baselineSource = view.baseline?.source;
  // The baseline stays known to the picker even when filters hide its row.
  const baselineFirst = view.baselineRun
    ? [
        view.baselineRun,
        ...view.runs.filter((r) => r.run_id !== view.baselineRun.run_id),
      ]
    : view.runs;
  const versionOn = view.version !== "all";
  const versionLabel = (() => {
    if (!versionOn) return null;
    const v = (view.versions ?? []).find(
      (x) => String(x.number) === String(view.version),
    );
    return `v${v?.number ?? view.version}`;
  })();
  const filterCount = view.activeFilters.length + (versionOn ? 1 : 0);

  const headline = useMemo(
    () => headlineVerdict(view.runs, view.baselineRun),
    [view.runs, view.baselineRun],
  );
  const cand = headline?.candidate ?? null;
  // options-truth L3 G8 P6 (L3/CLAIMS.md): the experiment Start from loads
  // (the highest ordinal, as experimentsOf orders them), so the label names
  // the experiment the drawer opens on.
  const latestExp = view.allRuns.length
    ? [...view.allRuns].sort(
        (a, b) =>
          (Number(b.experiment_ordinal) || 0) -
            (Number(a.experiment_ordinal) || 0) ||
          String(b.created_at).localeCompare(String(a.created_at)),
      )[0]
    : null;
  useRegisterCommands(
    "leaderboard",
    [
      {
        id: "leaderboard.new",
        label: "New experiment",
        keywords: ["run", "create", "experiment"],
        icon: "mdi:plus",
        run: () => openNew(),
        when: () => canRun,
      },
      {
        id: "leaderboard.startFromLast",
        label: latestExp
          ? `Start from last experiment (#${latestExp.experiment_ordinal})`
          : "Start from last experiment",
        keywords: ["rerun", "duplicate", "again", "copy"],
        icon: "mdi:content-duplicate",
        run: () => openNew({ prefill: { start_from: START_FROM_PRESET } }),
        when: () => canRun && !!latestExp,
      },
      {
        id: "leaderboard.compareSelected",
        label:
          selected.length > 1
            ? `Compare ${selected.length} selected runs`
            : "Compare selected",
        shortcut: "compareSelected",
        icon: "mdi:compare-horizontal",
        run: compareSelected,
        when: () =>
          selected.length > 0 &&
          !compareSelection(selected, view.baseline?.run_id).error,
      },
      {
        id: "leaderboard.pinBest",
        label: cand ? `Pin ${cand.label} as baseline` : "Pin as baseline",
        keywords: ["baseline", "pin", "best"],
        shortcut: "makeBaseline",
        icon: "mdi:pin-outline",
        run: () => cand && actions.pinBaseline(cand.run_id),
        when: () => canEdit && !!cand && cand.run_id !== view.pinnedRunId,
      },
      {
        id: "leaderboard.sideBySide",
        label:
          cand && view.baselineRun
            ? `Side by side: ${cand.label} vs ${view.baselineRun.label}`
            : "Side by side",
        keywords: ["compare", "trace", "steps", "workspace"],
        icon: "mdi:view-split-vertical",
        run: () =>
          navigate(
            taskCompareHref({
              datasetId: view.datasetId,
              runIds: [view.baselineRun.run_id, cand.run_id],
              baselineId: view.baselineRun.run_id,
              from: fromUrl,
            }),
          ),
        when: () =>
          !!cand &&
          !!view.baselineRun &&
          cand.run_id !== view.baselineRun.run_id,
      },
      {
        id: "leaderboard.filter",
        label: filtersOpen ? "Hide filters" : "Filter runs",
        keywords: ["filter", "version", "status", "model"],
        shortcut: "filter",
        icon: "mdi:filter-variant",
        run: () => setFiltersOpen((o) => !o),
        when: () => showTools,
      },
      {
        id: "leaderboard.evals",
        label: "Evals: pass rules and which evals new runs use",
        keywords: ["evals", "threshold", "pass"],
        icon: "mdi:check-decagram-outline",
        run: () => setOpenRunEvaluation(true),
      },
    ],
    [
      selected.length,
      cand?.run_id,
      latestExp?.run_id,
      canEdit,
      canRun,
      filtersOpen,
      showTools,
    ],
  );

  const savedState = {
    q: view.q,
    group: view.group,
    baseline: view.baseline?.source === "url" ? view.baseline.run_id ?? "" : "",
    filters: view.filters,
    version: view.version,
    agg: view.agg,
    charts: view.chartsParam,
  };
  const viewModified =
    !!view.q ||
    view.group !== "experiment" ||
    !!savedState.baseline ||
    view.filters.length > 0 ||
    view.version !== "all" ||
    view.agg !== "avg" ||
    !!view.chartsParam;
  const applyView = (st = {}) => {
    view.setQ(st.q || "");
    view.setGroup(st.group || "experiment");
    view.setBaseline(st.baseline || null);
    view.setFilters(Array.isArray(st.filters) ? st.filters : []);
    view.setVersion(st.version || "all");
    view.setAgg(st.agg || "avg");
    view.setChartsParam(
      st.charts === "1" || st.charts === 1
        ? true
        : st.charts === "0" || st.charts === 0
          ? false
          : null,
    );
  };

  // View › Show on this page: the Charts section (listed by ViewMenu), each
  // chart card, deltas and the score aggregate.
  const extras = [
    ...(enoughRuns
      ? CHART_CARDS.map((c) => ({
          id: `chart-${c.id}`,
          label: CHART_SHORT[c.id] ?? c.label,
          checked: prefs.chartCards.includes(c.id),
          onChange: (on) => {
            prefs.setChartCards((cards) =>
              on ? [...cards, c.id] : cards.filter((x) => x !== c.id),
            );
            if (on) prefs.setChartsOpen(true);
          },
        }))
      : []),
    {
      id: "deltas",
      label: "Deltas vs baseline",
      checked: prefs.showDeltas,
      onChange: prefs.setShowDeltas,
      description: "Each run's change against the baseline run, in every cell",
    },
    // L7c g5 (lb:view-toggles): Median is offered only while a score eval's
    // column is shown (pass/fail evals always show the pass rate, so with no
    // score column on screen the toggle would change nothing).
    ...((layout.columns ?? []).some(
      (c) =>
        !c.hidden &&
        view.metricDefs.some(
          (d) => d.key === c.id && d.output_type === "score",
        ),
    )
      ? [
          {
            id: "median",
            label: "Median for score evals",
            checked: view.agg === "median",
            onChange: (on) => view.setAgg(on ? "median" : "avg"),
            description:
              "Score evals show the median instead of the average. Pass/fail evals show the pass rate.",
          },
        ]
      : []),
  ];

  const searchField = (
    <Box
      ref={searchRef}
      sx={{ flex: "100 1 240px", minWidth: 200, maxWidth: 320 }}
    >
      <FormSearchField
        size="small"
        placeholder="Search runs   /"
        searchQuery={search}
        onChange={(e) => setSearch(e.target.value)}
        sx={{
          width: "100%",
          // A narrow window ends the hint with an ellipsis, never mid-word.
          "& input::placeholder": { textOverflow: "ellipsis" },
          "& input": { textOverflow: "ellipsis" },
        }}
        inputProps={{ "aria-label": "Search experiments, runs, models" }}
        InputProps={
          search
            ? {
                // EL#40: a visible clear; Esc in the field does the same.
                endAdornment: (
                  <InputAdornment position="end">
                    <CustomTooltip
                      show
                      title="Clear search · Esc"
                      size="small"
                      placement="top"
                      arrow
                    >
                      <IconButton
                        size="small"
                        aria-label="Clear search"
                        onClick={() => {
                          setSearch("");
                          view.setQ("");
                        }}
                        sx={{ mr: -0.75 }}
                      >
                        <Iconify icon="mdi:close" width={16} />
                      </IconButton>
                    </CustomTooltip>
                  </InputAdornment>
                ),
              }
            : undefined
        }
        onKeyDown={(e) => {
          if (e.key === "Escape" && search) {
            e.stopPropagation();
            setSearch("");
            view.setQ("");
          }
        }}
      />
    </Box>
  );

  const filterButton = (
    <KeyHintTooltip
      title={
        filterCount
          ? `Filter runs · ${fmtCount(filterCount, "filter")} on`
          : "Filter runs by version, status, model, scores…"
      }
      action="filter"
    >
      <TriggerButton
        label="Filter"
        icon="mdi:filter-variant"
        active={filtersOpen || filterCount > 0}
        aria-expanded={filtersOpen}
        aria-label={filterCount ? `Filter, ${filterCount} on` : "Filter"}
        onClick={() => setFiltersOpen((o) => !o)}
        endIcon={
          filterCount ? (
            <Box
              component="span"
              sx={{
                minWidth: 18,
                height: 18,
                px: 0.5,
                borderRadius: "9px",
                bgcolor: "primary.main",
                color: "primary.contrastText",
                typography: "s3",
                lineHeight: "18px",
                textAlign: "center",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {filterCount}
            </Box>
          ) : undefined
        }
      />
    </KeyHintTooltip>
  );

  const versionChip = versionOn ? (
    <CustomTooltip
      show
      title={`Only runs on dataset ${versionLabel} · remove to see every version`}
      size="small"
      arrow
    >
      <Chip
        size="small"
        variant="outlined"
        label={versionLabel}
        onDelete={() => view.setVersion("all")}
        deleteIcon={
          <Iconify
            icon="mdi:close"
            width={14}
            aria-label={`Remove the ${versionLabel} filter`}
          />
        }
        data-testid="version-filter-chip"
        sx={{ typography: "s2", height: 26, flexShrink: 0 }}
      />
    </CustomTooltip>
  ) : null;

  const baselineControl = (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
      <CustomTooltip
        show={!!view.baseline}
        title={baselineTip(view)}
        size="small"
        placement="bottom"
        arrow
        enterDelay={400}
      >
        <Box sx={{ "& > .MuiButton-root": { maxWidth: 260 } }}>
          <BaselinePicker
            datasetId={view.datasetId}
            value={view.baseline?.run_id ?? null}
            pinnedRunId={view.pinnedRunId}
            runsInView={baselineFirst}
            onChange={(runId) => view.setBaseline(runId)}
          />
        </Box>
      </CustomTooltip>
      {baselineSource === "auto" && (
        <CustomTooltip
          show
          title="No run is pinned. Deltas use the earliest completed run."
          placement="bottom"
          arrow
          size="small"
        >
          <Typography typography="s2" color="text.secondary">
            Auto
          </Typography>
        </CustomTooltip>
      )}
      {baselineSource === "url" && (
        <CustomTooltip
          show
          title="Baseline for this view only (from the link). Reset goes back to the pinned one."
          placement="bottom"
          arrow
          size="small"
        >
          <Button
            size="small"
            onClick={() => view.setBaseline(null)}
            sx={{ minWidth: 0, px: 0.75, typography: "s2" }}
          >
            Reset
          </Button>
        </CustomTooltip>
      )}
    </Stack>
  );

  return (
    <Box sx={{ pt: 1 }}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        data-testid="leaderboard-toolbar"
        sx={{ px: 1.5, pb: 0.5, minHeight: 40, minWidth: 0 }}
      >
        {showTools && (
          <>
            {searchField}
            {filterButton}
            {versionChip}
          </>
        )}
        <Box sx={{ flex: 1 }} />
        {/* IT-R4 G2 P7: Columns, Evals, Group and Baseline live in ONE
            `View` popover (screen 06 budget); `More` keeps the oneview
            ViewMenu (charts, deltas, saved views). */}
        {showTools ? (
          <LeaderboardViewButton
            columnsText={`${Math.max(0, (layout.columns ?? []).filter((c) => !c.hidden).length)} shown${layout.hiddenCount ? ` · ${layout.hiddenCount} hidden` : ""}`}
            onColumns={() => layout.openMenu()}
            evalsText={
              loaded
                ? `${evalCount} · pass rules${noThreshold ? ` · ${noThreshold} without a threshold` : ""}`
                : "Pass rules"
            }
            evalsAlert={noThreshold > 0}
            onEvals={() => setOpenRunEvaluation(true)}
            group={
              <GroupBySelect
                value={view.group}
                onChange={view.setGroup}
                onExpandAll={() => kb()?.expandAll?.()}
                onCollapseAll={() => kb()?.collapseAll?.()}
              />
            }
            baseline={baselineControl}
            runs={
              <EveryRunSwitch on={view.everyAll} onChange={view.setEveryAll} />
            }
            changed={viewModified}
            more={
              <Stack direction="row" alignItems="center" gap={1}>
                <Typography typography="s2" color="text.secondary">
                  Charts, deltas, saved views
                </Typography>
                <ViewMenu
                  surface={LB_SURFACE}
                  scopeId={view.datasetId}
                  columns={layout}
                  extras={extras}
                  savedViews={{ state: savedState, onApply: applyView }}
                  isModified={viewModified}
                  onReset={() => applyView({})}
                  iconOnly
                />
              </Stack>
            }
          />
        ) : null}
        {/* IT-R3 G2 P5: New experiment lives in the dataset header on
            every tab (DatasetHeaderBar), one primary per screen. */}
        {selected.length > 0 && (
          <SelectionBar
            selected={selected}
            allRuns={view.allRuns}
            datasetId={view.datasetId}
            baselineId={view.baseline?.run_id}
            pinnedRunId={view.pinnedRunId}
            canEdit={canEdit}
            canRun={canRun}
            canDelete={canDelete}
            tips={gates.tips}
            fromUrl={fromUrl}
            navigate={navigate}
            onPin={(id) => actions.pinBaseline(id)}
            onRerun={actions.requestRerun}
            onDelete={actions.requestDelete}
            onCancel={clearSelection}
            onFilterToSelected={(ids) => {
              view.setFilters(withRunFilter(view.filters, ids));
              clearSelection();
            }}
          />
        )}
      </Stack>
      <LeaderboardFilters
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        filters={view.filters}
        onChange={view.setFilters}
        runs={view.allRuns}
        metricDefs={view.metricDefs}
        version={view.version}
        versions={view.versions}
        onVersion={view.setVersion}
      />
      {actions.dialogs}
    </Box>
  );
};

ExperimentTabHeader.propTypes = {
  tabRef: PropTypes.object,
  experimentSearch: PropTypes.string,
  setExperimentSearch: PropTypes.func,
  rowSelected: PropTypes.array,
  setRowSelected: PropTypes.func,
  setCurrentTab: PropTypes.func,
};

export default ExperimentTabHeader;
