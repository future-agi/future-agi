// The Paths tab (R4 implementation, R4-SPEC D14): which way a run's tries go, and
// how often each way fails. Answer first (PathSentence: the path that
// fails most, with where node identity comes from), then `Top paths` and
// the run's aggregate graph (AgentGraph / AgentPath). `Compare with ▾`
// adds per-run shares, `New in` / `Gone in` chips and the overlay graph.
// URL: `prun`, `pcmp` (`none` = no compare), `pmode=graph|sankey|table`,
// `pfail=1` (failing paths only). Keys (surface `paths`): J/K, Enter, G, I.
import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRunConfigs } from "src/api/develop/r2";
import { useRunPaths } from "src/api/develop/r4";
import { runLabel, runShortName } from "src/sections/common/Runs/runLabel";
import { useRunColors } from "src/sections/common/Runs/runColors";
import EmptyState from "src/sections/common/ux/EmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { ItemsMenu, useLayoutPrefs } from "src/sections/common/ux/oneview";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import {
  comparePaths,
  featuredPath,
} from "src/sections/common/Trajectory/pathModel";
import ViewSwitch from "src/sections/common/Trajectory/ViewSwitch";
import PathSentence from "./PathSentence";
import PathsTable, { PATH_COLUMNS } from "./PathsTable";
import PathsGraph from "./PathsGraph";
import PathsCompare from "./PathsCompare";
import StepCompareTable from "./StepCompareTable";
import { triesThrough } from "./stepCompare";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useRootCauses } from "../matrix/useMatrixData";
import { useIterateStore } from "../iterate/iterateStore";
import { canIterate, llmNodes } from "../iterate/iterateModel";
import { VIEWER_TIP } from "../iterate/IterateButton";
import usePagePair from "./usePagePair";
import { causeForPath, fixReasonOf, fixTargetOf, ownerNodeOf } from "./fixPath";

export const NO_PATHS_TEXT = "This run has no traces, so there are no paths.";
const TOP = 5;
const WIDE = 1100;
const MODES = ["graph", "sankey", "table"];
const MODE_OPTIONS = {
  graph: {
    label: "Graph",
    icon: "mdi:graph-outline",
    tip: "Graph — the run's paths as one graph (G)",
  },
  sankey: {
    label: "Sankey",
    icon: "mdi:chart-sankey",
    tip: "Sankey — how many tries flow through each step",
  },
  table: {
    label: "Table",
    icon: "mdi:table",
    tip: "Table — paths only, full width (G)",
  },
};

function useWidth(ref) {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.round(e.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

// A run whose config hasn't loaded yet has no name (never its raw id).
const nameOf = (run) =>
  run && !run.pending
    ? [runLabel(run), runShortName(run)].filter(Boolean).join(" ")
    : "";

/** Paths containing the node (by name), for `I` / node click. */
const NOT_IN_RELEASE = "Not available in this release";
const throughNode = (steps, node) => (steps ?? []).some((s) => s.name === node);

export default function PathsView({
  datasetId,
  experimentId,
  runIds = [],
  baselineId: baselineProp,
}) {
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const rootRef = useRef(null);
  const width = useWidth(rootRef);
  const wide = width === 0 || width >= WIDE;
  const set = (patch) =>
    setSp(
      (prev) => {
        const next = new URLSearchParams(prev);
        Object.entries(patch).forEach(([k, v]) =>
          v == null || v === "" ? next.delete(k) : next.set(k, v),
        );
        return next;
      },
      { replace: true },
    );

  const { data: cfg, held: runsHeld } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const runs = useMemo(() => {
    const byId = new Map((cfg?.results ?? []).map((r) => [r.run_id, r]));
    return runIds.map((id) => byId.get(id) ?? { run_id: id, pending: true });
  }, [cfg, runIds]);
  const runOf = (id) => runs.find((r) => r.run_id === id) ?? null;

  // iterate r1 G3 (P4): Paths opens on the page's pair — the candidate the
  // header names vs the page's baseline (usePagePair).
  const { baselineId, candidateId: candidate } = usePagePair({
    datasetId,
    experimentId,
    runIds,
    baselineId: baselineProp,
    search: sp.toString(),
  });
  const urlRun = sp.get("prun");
  const prun = runIds.includes(urlRun)
    ? urlRun
    : candidate ?? runIds.find((id) => id !== baselineId) ?? runIds[0] ?? null;
  const urlCmp = sp.get("pcmp");
  const pcmp =
    urlCmp === "none"
      ? null
      : runIds.includes(urlCmp) && urlCmp !== prun
        ? urlCmp
        : baselineId && baselineId !== prun && runIds.includes(baselineId)
          ? baselineId
          : null;
  const mode = MODES.includes(sp.get("pmode"))
    ? sp.get("pmode")
    : wide
      ? "graph"
      : "table";
  const failingOnly = sp.get("pfail") === "1";

  const colorMap = useRunColors(runIds, baselineId);
  const colors = {
    a: colorMap[prun]?.solid ?? null,
    b: pcmp ? colorMap[pcmp]?.solid ?? null : null,
  };
  const labelOf = (id) => {
    const r = runOf(id);
    return r && !r.pending ? runLabel(r) : "";
  };
  const labels = {
    a: labelOf(prun) || "This run",
    b: pcmp ? labelOf(pcmp) || "the other run" : null,
  };

  const A = useRunPaths(prun, { enabled: Boolean(prun) });
  const B = useRunPaths(pcmp, { enabled: Boolean(pcmp) });
  const agg = A.data ?? null;
  const cmpAgg = pcmp ? B.data ?? null : null;
  const compare = Boolean(pcmp && cmpAgg);

  const [prefs, patchPrefs, resetPrefs] = useLayoutPrefs("paths");
  const optional = prefs.extras?.pathCols ?? [];
  const edgeLabels = Boolean(prefs.extras?.edgeLabels);
  const [showAll, setShowAll] = useState(false);
  const [node, setNode] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [menu, setMenu] = useState(null);

  const allRows = useMemo(() => {
    if (!agg) return [];
    return compare ? comparePaths(agg, cmpAgg) : agg.paths ?? [];
  }, [agg, cmpAgg, compare]);
  const filtered = allRows.filter((r) => {
    const p = compare ? r.a ?? r.b : r;
    if (failingOnly && !(compare ? (r.failA ?? r.failB) > 0 : r.failed > 0))
      return false;
    if (node && !throughNode(p.steps, node)) return false;
    return true;
  });
  const rows = showAll || node ? filtered : filtered.slice(0, TOP);
  const cur = Math.min(cursor, Math.max(0, rows.length - 1));

  // iterate r1 G3 (P1): `Fix this path` → Test a fix on the run the path
  // fails in, on the node that emitted the failing step, with the reason.
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const causesQ = useRootCauses(
    { datasetId, runIds, baselineId },
    { enabled: Boolean(agg?.paths?.length) },
  );
  // dataset integration FE-0: `Fix this path` only for a run Test a fix applies to.
  const fixTarget = (r) => {
    const t = fixTargetOf(r, { compare, prun, pcmp });
    const run = t ? runOf(t.runId) : null;
    return t && (!run || canIterate(run)) ? t : null;
  };
  const fixFor = (r) => {
    const t = fixTarget(r);
    return t
      ? { ...t, label: nameOf(runOf(t.runId)) || labelOf(t.runId) }
      : null;
  };
  const fixPath = (r) => {
    const t = fixTarget(r);
    if (!t || !canEdit) return;
    const run = runOf(t.runId);
    const cause = causeForPath(causesQ.data?.causes, t.runId, t.path);
    useIterateStore.getState().open({
      runId: t.runId,
      entry: {
        runId: t.runId,
        // v2.1 g4 (dock:fix-reason): the fix runs on this path's rows, not
        // every failing row of the run.
        scope: t.path.example_rows?.length ? "rows" : "failed",
        rows: t.path.example_rows ?? [],
        change: null,
        cause: cause?.id ?? null,
        node: ownerNodeOf(t.path, llmNodes(run?.config), cause),
        reason: fixReasonOf(t.path, cause),
        pathId: t.path.id,
      },
    });
  };

  const openExamples = (r) => {
    const p = compare ? r?.a ?? r?.b : r;
    const first = p?.example_rows?.[0];
    if (!first) return;
    navigate(
      taskCompareHref({
        datasetId,
        runIds: [prun, pcmp].filter(Boolean),
        baselineId: pcmp ?? prun,
        rowId: first,
        rail: "differ",
      }),
    );
  };

  useShortcuts(
    "paths",
    {
      next: () => setCursor((c) => Math.min(rows.length - 1, c + 1)),
      prev: () => setCursor((c) => Math.max(0, c - 1)),
      open: () => openExamples(rows[cur]),
      graphView: () => set({ pmode: mode === "table" ? "graph" : "table" }),
      inspectNode: () => {
        const p = compare ? rows[cur]?.a ?? rows[cur]?.b : rows[cur];
        const steps = p?.steps ?? [];
        if (!steps.length) return;
        const at = node ? steps.findIndex((s) => s.name === node) : -1;
        setNode(steps[(at + 1) % steps.length].name);
      },
    },
    { enabled: Boolean(agg?.paths?.length) },
  );

  const runMenuItems = (which) =>
    which === "prun"
      ? runs.map((r) => ({
          id: r.run_id,
          label: nameOf(r),
          secondary: r.run_id === baselineId ? "Baseline" : undefined,
          checked: r.run_id === prun,
          run: () => {
            set({
              prun: r.run_id,
              ...(r.run_id === pcmp ? { pcmp: null } : {}),
            });
            setNode(null);
            setCursor(0);
          },
        }))
      : [
          ...runs
            .filter((r) => r.run_id !== prun)
            .map((r) => ({
              id: r.run_id,
              label: nameOf(r),
              secondary: r.run_id === baselineId ? "Baseline" : undefined,
              checked: r.run_id === pcmp,
              run: () => set({ pcmp: r.run_id }),
            })),
          { divider: true },
          {
            id: "none",
            label: "Don't compare",
            icon: "mdi:close",
            checked: !pcmp,
            run: () => set({ pcmp: "none" }),
          },
        ];
  const colItems = [
    ...PATH_COLUMNS.filter((c) => !c.always && (compare || !c.compareOnly)).map(
      (c) => ({
        id: c.id,
        label: c.label,
        checked: optional.includes(c.id),
        run: () =>
          patchPrefs({
            extras: {
              pathCols: optional.includes(c.id)
                ? optional.filter((x) => x !== c.id)
                : [...optional, c.id],
            },
          }),
      }),
    ),
    { divider: true },
    {
      id: "labels",
      label: "Edge labels on the graph",
      checked: edgeLabels,
      run: () =>
        patchPrefs({ extras: { edgeLabels: edgeLabels ? undefined : true } }),
    },
    {
      id: "reset",
      label: "Reset layout",
      icon: "mdi:restore",
      run: () => resetPrefs(),
    },
  ];

  // ------------------------------------------------------------ states
  let content;
  // This build's backend has no paths route: one calm line, no request.
  if (A.held)
    content = (
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ p: 2 }}
        data-testid="paths-not-in-release"
      >
        {NOT_IN_RELEASE}
      </Typography>
    );
  else if (!prun)
    content = (
      <EmptyState
        icon="mdi:source-branch"
        title="No runs to show paths for"
        body="Run an experiment on this dataset; each run's paths show here."
      />
    );
  else if (A.isError)
    content = (
      <ErrorState
        thing="this run's paths"
        error={A.error}
        onRetry={() => A.refetch()}
        requestId={A.error?.response?.headers?.["x-request-id"]}
      />
    );
  else if (A.isLoading || !agg)
    content = (
      <Stack gap={1} sx={{ p: 2 }} data-testid="paths-loading">
        <Skeleton width="70%" height={24} />
        <Skeleton width="40%" height={16} />
        {Array.from({ length: TOP }).map((_, i) => (
          <Skeleton key={i} variant="rounded" height={36} />
        ))}
      </Stack>
    );
  else if (!agg.paths?.length)
    content = (
      <EmptyState
        icon="mdi:timeline-remove-outline"
        title={`${labels.a} has no paths`}
        body={NO_PATHS_TEXT}
      />
    );
  else {
    const graphPane =
      mode === "table" ? null : compare && mode === "graph" ? (
        <PathsCompare
          // P4: the legend names the runs (`Only in #2D Claude Code`).
          names={{
            a: nameOf(runOf(prun)) || labels.a,
            b: nameOf(runOf(pcmp)) || labels.b,
          }}
          agg={agg}
          compareAgg={cmpAgg}
          labels={labels}
          colors={colors}
          edgeLabels={edgeLabels}
          onNodeClick={(id) => setNode(id === node ? null : id)}
        />
      ) : (
        <PathsGraph
          agg={agg}
          mode={mode}
          edgeLabels={edgeLabels}
          selected={node}
          onNodeClick={(id) => setNode(id === node ? null : id)}
        />
      );
    const table = (
      <Stack sx={{ minHeight: 0, minWidth: 0, flex: 1 }}>
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ px: 1, pb: 0.5, minHeight: 28 }}
        >
          <Typography typography="s2" fontWeight="fontWeightSemiBold">
            Top paths
          </Typography>
          {node ? (
            <Chip
              size="small"
              label={`Through ${node}`}
              onDelete={() => setNode(null)}
              sx={{ height: 20, typography: "s3" }}
            />
          ) : null}
          <Box sx={{ flex: 1 }} />
          {filtered.length > TOP && !node ? (
            <Button
              size="small"
              color="inherit"
              onClick={() => setShowAll((v) => !v)}
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {showAll
                ? `Top ${TOP} only`
                : `Show all paths (${filtered.length})`}
            </Button>
          ) : null}
        </Stack>
        {rows.length ? (
          <PathsTable
            rows={rows}
            compare={compare}
            labels={labels}
            colors={colors}
            columns={optional}
            cursor={cur}
            onCursor={setCursor}
            onOpen={openExamples}
            fixFor={fixFor}
            onFix={fixPath}
            fixDisabledReason={canEdit ? null : VIEWER_TIP}
          />
        ) : (
          <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
            {failingOnly
              ? "No path fails. Turn off Failing paths only to see every path."
              : "No path goes through this step."}
          </Typography>
        )}
      </Stack>
    );
    // The sentence's path (fails most), as a table row for `Open examples`.
    const fp = featuredPath(agg);
    const featured = fp
      ? allRows.find((r) => r.id === fp.id) ?? null
      : rows[0] ?? null;
    const exN = Math.min(
      3,
      (compare ? featured?.a ?? featured?.b : featured)?.example_rows?.length ??
        0,
    );
    content = (
      <Stack
        sx={{
          flex: 1,
          minHeight: 0,
          gap: 1.5,
          // the interface contract: with the per-step table below, the pane scrolls and
          // the paths + graph keep their height.
          ...(compare ? { overflowY: "auto" } : {}),
        }}
      >
        <PathSentence
          agg={agg}
          compareAgg={cmpAgg}
          label={labels.a}
          compareLabel={labels.b}
          action={
            exN ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => openExamples(featured)}
                startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
                sx={{ flexShrink: 0, ml: "auto", whiteSpace: "nowrap" }}
              >
                {`Open ${exN} examples side by side`}
              </Button>
            ) : null
          }
        />
        <Box
          sx={{
            flex: compare ? "0 0 auto" : 1,
            minHeight: compare ? 440 : 0,
            height: compare ? 440 : undefined,
            display: "flex",
            flexDirection: wide ? "row" : "column",
            gap: 1.5,
          }}
        >
          {!wide && graphPane ? null : table}
          {graphPane ? (
            <Box
              sx={{
                flex: wide ? "0 0 46%" : 1,
                minWidth: 0,
                minHeight: 320,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                overflow: "hidden",
              }}
            >
              {graphPane}
            </Box>
          ) : null}
        </Box>
        {/* the interface contract (CP-08): per-step metrics, baseline → candidate. */}
        {compare ? (
          <Box sx={{ flexShrink: 0, overflowX: "auto" }}>
            <StepCompareTable
              baseAgg={cmpAgg}
              candAgg={agg}
              labels={{ base: labels.b, cand: labels.a }}
              selected={node}
              onSelect={(id) => setNode(id === node ? null : id)}
              onOpenTries={(id) => {
                const rowId = triesThrough([agg, cmpAgg], id)[0];
                if (rowId)
                  navigate(
                    taskCompareHref({
                      datasetId,
                      runIds: [prun, pcmp].filter(Boolean),
                      baselineId: pcmp ?? prun,
                      rowId,
                      rail: "differ",
                    }),
                  );
              }}
            />
          </Box>
        ) : null}
      </Stack>
    );
  }

  return (
    <Stack
      ref={rootRef}
      sx={{ px: 2.5, py: 1.5, flex: 1, minHeight: 0, gap: 1.5 }}
      data-testid="paths-view"
    >
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <Button
          size="small"
          color="inherit"
          onClick={(e) => setMenu({ el: e.currentTarget, which: "prun" })}
          disabled={Boolean(runsHeld && !cfg)}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{ typography: "s2", border: "1px solid", borderColor: "divider" }}
          data-testid="paths-run"
        >
          <Box
            component="span"
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: colors.a,
              mr: 0.75,
            }}
          />
          {nameOf(runOf(prun)) ||
            (cfg ? "Pick a run" : runsHeld ? NOT_IN_RELEASE : "Loading runs…")}
        </Button>
        <Button
          size="small"
          color="inherit"
          onClick={(e) => setMenu({ el: e.currentTarget, which: "pcmp" })}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{ typography: "s2" }}
          data-testid="paths-compare"
        >
          {pcmp ? `Compare with ${labels.b}` : "Compare with"}
        </Button>
        <Box sx={{ flex: 1 }} />
        {/* P4: 32 px targets for Graph · Sankey · Table. */}
        <Box
          sx={{
            "& .MuiToggleButtonGroup-root .MuiToggleButton-root": {
              minHeight: 32,
            },
          }}
        >
          <ViewSwitch
            value={mode}
            onChange={(v) => set({ pmode: v })}
            options={MODES}
            labels={MODE_OPTIONS}
            ariaLabel="Paths view"
          />
        </Box>
        <CustomTooltip
          show
          size="small"
          type="black"
          title="Show only paths with at least one failed try"
        >
          <Button
            size="small"
            color={failingOnly ? "primary" : "inherit"}
            onClick={() => set({ pfail: failingOnly ? null : "1" })}
            startIcon={<Iconify icon="mdi:filter-variant" width={16} />}
            sx={{ typography: "s2" }}
          >
            {failingOnly ? "Failing paths only" : "Filter"}
          </Button>
        </CustomTooltip>
        <CustomTooltip
          show
          size="small"
          type="black"
          title="Columns and graph labels"
        >
          <IconButton
            size="small"
            aria-label="Columns and graph labels"
            onClick={(e) => setMenu({ el: e.currentTarget, which: "cols" })}
          >
            <Iconify icon="mdi:tune-variant" width={18} />
          </IconButton>
        </CustomTooltip>
        <ItemsMenu
          open={Boolean(menu)}
          anchorEl={menu?.el}
          onClose={() => setMenu(null)}
          items={
            menu?.which === "cols"
              ? colItems
              : menu
                ? runMenuItems(menu.which)
                : []
          }
        />
      </Stack>
      {content}
    </Stack>
  );
}

PathsView.propTypes = {
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
};
