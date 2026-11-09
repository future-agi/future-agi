// Task compare · Graph (R4 lane T, `depth=graph`): the baseline lane and one
// other lane on ONE overlay graph (DiffGraph over Observe's AgentGraph),
// `Compare with ▾` at 3–4 lanes (default: the lane whose graph differs most
// from the baseline), `gdiff=0` = per-lane graphs side by side. A node
// click (or `I`) opens NodeSpansSplit under the graph: that node's spans
// from both runs with a visit pager; `gsel=` keeps it in the URL.
import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { ItemsMenu, useLayoutPrefs } from "src/sections/common/ux/oneview";
import {
  graphOfTree,
  isStepFlow,
} from "src/sections/common/Trajectory/flowGraph";
import DiffGraph, {
  SingleGraph,
} from "src/sections/common/Trajectory/DiffGraph";
import { QualityLine } from "src/sections/common/Trajectory/GraphLegend";
import NodeSpansSplit from "src/sections/common/Trajectory/NodeSpansSplit";
import { mostDifferent } from "src/sections/common/Trajectory/runGraphDiff";

const SPLIT_H = 300;
// Below this view height the node split sits right of the graph (laptops).
const SIDE_BELOW_H = 760;

function useSize(ref) {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setSize({
        w: Math.round(e.contentRect.width),
        h: Math.round(e.contentRect.height),
      }),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}
const safeGraph = (tree) => {
  try {
    return tree ? graphOfTree(tree) : null;
  } catch {
    return null;
  }
};
/** Span ids of a node (by name) in one lane's tree. */
const spanIdsOf = (graph, name) => {
  const node = graph?.nodes?.find((n) => n.name === name || n.id === name);
  return node ? graph.nodeToSpanIds?.[node.id] ?? [] : [];
};

export default function TcGraphView({
  graphs,
  gsel,
  onGsel,
  overlay = true,
  onOverlay,
  depthSwitch,
  keysEnabled = true,
  onOpenStep,
}) {
  const base = graphs[0] ?? null;
  const others = graphs.slice(1).filter((g) => g.tree);
  const built = useMemo(
    () => Object.fromEntries(graphs.map((g) => [g.id, safeGraph(g.tree)])),
    [graphs],
  );
  const [cmpId, setCmpId] = useState(null);
  const defaultCmp = useMemo(() => {
    if (others.length <= 1) return others[0]?.id ?? null;
    return (
      mostDifferent(
        built[base?.id],
        others.map((o) => ({ id: o.id, graph: built[o.id] })),
      ) ?? others[0].id
    );
  }, [built, base?.id, others]);
  const cmp =
    graphs.find((g) => g.id === cmpId && g.tree) ??
    graphs.find((g) => g.id === defaultCmp) ??
    null;
  const [menu, setMenu] = useState(null);
  const [fitKey, setFitKey] = useState(0);
  // `Fit` fits the whole graph, below the reading zoom if it must (IT-R1).
  // IT-R2 G4 P5: it opens fitted (and refits on resize: GraphBox), so
  // nothing is cut off at the bottom.
  // IT-R5 G4 P1: it opens fitted to the graph's WIDTH at a reading zoom
  // (labels ≥ 11 px) and pans by drag or wheel; `Fit` (0) re-fits.
  const fitAll = false;
  const fitGraph = () => setFitKey((k) => k + 1);
  // A node click opens that step in Steps (the lane's first visit).
  const pickNode = (g, n) => {
    if (onOpenStep && n?.name) {
      const spanIds = Array.isArray(n.spanIds)
        ? n.spanIds
        : n.spanIds?.[g === cmp ? "b" : "a"] ?? [];
      onOpenStep({ laneId: g?.id, name: n.name, spanIds });
      return;
    }
    onGsel(n.name === gsel ? null : n.name);
  };
  // Edge labels (`2×`) are one click away and remembered per user.
  const [prefs, patchPrefs] = useLayoutPrefs("taskCompare");
  const edgeLabels = Boolean(prefs.extras?.edgeLabels);
  const rootRef = useRef(null);
  const size = useSize(rootRef);
  const sideSplit = size.h > 0 && size.h < SIDE_BELOW_H && size.w >= 820;

  const pair = base && cmp ? [base, cmp] : [];
  // IT-R4 G4 P6: the overlay needs a full graph on both sides (DiffGraph's
  // rule); otherwise the switch is off and disabled with the reason.
  const fullGraph = (g) =>
    Boolean(g?.tree) && (g.quality?.quality === "full" || isStepFlow(g.tree));
  const canOverlay = Boolean(base && cmp && fullGraph(base) && fullGraph(cmp));
  const nodeNames = useMemo(() => {
    const out = [];
    const failed = pair.map((g) => g.failedName).filter(Boolean);
    failed.forEach((n) => !out.includes(n) && out.push(n));
    pair.forEach((g) =>
      (built[g.id]?.nodes ?? []).forEach((n) => {
        if (n.type === "start" || n.type === "end") return;
        if (!out.includes(n.name)) out.push(n.name);
      }),
    );
    return out;
  }, [pair, built]); // eslint-disable-line react-hooks/exhaustive-deps

  useShortcuts(
    "taskCompare",
    {
      inspectNode: {
        run: () => {
          if (!nodeNames.length) return;
          const i = gsel ? nodeNames.indexOf(gsel) : -1;
          onGsel(nodeNames[(i + 1) % nodeNames.length]);
        },
        when: () => nodeNames.length > 0,
        reason: "Needs a graph with steps",
      },
    },
    { enabled: keysEnabled, register: false },
  );

  const sides = gsel
    ? pair.map((g, i) => ({
        key: i ? "b" : "a",
        label: g.name,
        color: g.color,
        tree: g.tree,
        spanIds: spanIdsOf(built[g.id], gsel),
      }))
    : [];

  const toolbar = (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        px: 1.5,
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        minWidth: 0,
      }}
    >
      {depthSwitch}
      {graphs.length > 2 ? (
        <>
          <Button
            size="small"
            color="inherit"
            onClick={(e) => setMenu(e.currentTarget)}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            sx={{ typography: "s2", flexShrink: 0 }}
            data-testid="graph-compare-with"
          >
            {cmp ? `${base?.name} vs ${cmp.name}` : "Compare with"}
          </Button>
          <ItemsMenu
            open={Boolean(menu)}
            anchorEl={menu}
            onClose={() => setMenu(null)}
            items={graphs.slice(1).map((g) => ({
              id: g.id,
              label: `Compare ${base?.name} with ${g.name}`,
              secondary: g.id === defaultCmp ? "Most different" : undefined,
              checked: g.id === cmp?.id,
              disabled: !g.tree,
              reason: "No trace for this try",
              run: () => setCmpId(g.id),
            }))}
          />
        </>
      ) : null}
      <Box sx={{ flex: 1 }} />
      <CustomTooltip
        show
        size="small"
        type="black"
        title={
          canOverlay
            ? "One graph of both runs (on), or each run's own graph side by side (off)"
            : `Diff overlay is off: ${[base, cmp]
                .filter((g) => g && !fullGraph(g))
                .map((g) => g.name ?? "a run")
                .join(
                  " and ",
                )} has no declared graph (its nodes are the tools it called), so each run shows its own graph.`
        }
      >
        <FormControlLabel
          disabled={!canOverlay}
          control={
            <Switch
              size="small"
              checked={overlay && canOverlay}
              disabled={!canOverlay}
              onChange={(e) => onOverlay(e.target.checked)}
            />
          }
          label={<Typography typography="s2">Diff overlay</Typography>}
          sx={{ mr: 0, flexShrink: 0 }}
        />
      </CustomTooltip>
      <CustomTooltip
        show
        size="small"
        type="black"
        title="Show how many times each edge was taken"
      >
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={edgeLabels}
              onChange={(e) =>
                patchPrefs({
                  extras: { edgeLabels: e.target.checked ? true : undefined },
                })
              }
            />
          }
          label={<Typography typography="s2">Edge labels</Typography>}
          sx={{ mr: 0, flexShrink: 0 }}
        />
      </CustomTooltip>
      <CustomTooltip show size="small" type="black" title="Fit the graph (0)">
        <IconButton size="small" aria-label="Fit the graph" onClick={fitGraph}>
          <Iconify icon="mdi:fit-to-screen-outline" width={18} />
        </IconButton>
      </CustomTooltip>
    </Stack>
  );

  let graph;
  if (!base?.tree)
    graph = (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        {base?.loading
          ? "Loading the graphs…"
          : `${base?.name ?? "The baseline"} has no trace for this try, so there is no graph to draw.`}
      </Typography>
    );
  else if (overlay && cmp)
    graph = (
      <DiffGraph
        key={fitKey}
        trees={{ a: base.tree, b: cmp.tree }}
        qualities={{ a: base.quality, b: cmp.quality }}
        labels={{ a: base.name, b: cmp.name }}
        colors={{ a: base.color, b: cmp.color }}
        failed={{ a: base.failedName, b: cmp.failedName }}
        selected={gsel}
        edgeLabels={edgeLabels}
        fitAll={fitAll}
        onSelectNode={(n) => {
          if (!onOpenStep) return onGsel(n.name === gsel ? null : n.name);
          // Overlay: open it in the lane that ran it (the candidate first).
          const inB = (n.spanIds?.b ?? []).length > 0;
          return onOpenStep({
            laneId: inB ? cmp.id : base.id,
            name: n.name,
            spanIds: inB ? n.spanIds.b : n.spanIds?.a ?? [],
          });
        }}
      />
    );
  else
    graph = (
      <Box
        key={fitKey}
        sx={{
          display: "grid",
          gridTemplateColumns: `repeat(${graphs.length}, minmax(0, 1fr))`,
          height: "100%",
        }}
        data-testid="graph-small-multiples"
      >
        {graphs.map((g, i) => (
          <Stack
            key={g.id}
            sx={{
              minWidth: 0,
              minHeight: 0,
              borderLeft: i ? "1px solid" : 0,
              borderColor: "divider",
            }}
          >
            <Box
              sx={{
                px: 1.25,
                py: 0.5,
                borderTop: "3px solid",
                borderTopColor: g.color ?? "divider",
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <QualityLine quality={g.quality} prefix={g.name} />
            </Box>
            <Box sx={{ flex: 1, minHeight: 0 }}>
              {g.tree ? (
                <SingleGraph
                  tree={g.tree}
                  failedName={g.failedName}
                  selected={gsel}
                  edgeLabels={edgeLabels}
                  fitAll={fitAll}
                  onNodeClick={(n) => pickNode(g, n)}
                />
              ) : (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ p: 2 }}
                >
                  No trace for this try.
                </Typography>
              )}
            </Box>
          </Stack>
        ))}
      </Box>
    );

  return (
    <Stack
      ref={rootRef}
      sx={{ flex: 1, minHeight: 0, bgcolor: "background.paper" }}
      data-testid="tc-graph"
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === "0" && !e.metaKey && !e.ctrlKey) fitGraph();
      }}
    >
      {toolbar}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: sideSplit ? "row" : "column",
        }}
      >
        <Box
          sx={{ flex: 1, minHeight: 200, minWidth: 0, position: "relative" }}
        >
          {graph}
        </Box>
        {gsel && sides.length ? (
          <NodeSpansSplit
            node={{ name: gsel }}
            sides={sides}
            height={SPLIT_H}
            layout={sideSplit ? "side" : "below"}
            onClose={() => onGsel(null)}
          />
        ) : null}
      </Box>
    </Stack>
  );
}

TcGraphView.propTypes = {
  graphs: PropTypes.arrayOf(PropTypes.object).isRequired,
  gsel: PropTypes.string,
  onGsel: PropTypes.func.isRequired,
  overlay: PropTypes.bool,
  onOverlay: PropTypes.func,
  depthSwitch: PropTypes.node,
  keysEnabled: PropTypes.bool,
  onOpenStep: PropTypes.func,
};
