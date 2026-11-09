// One-view primitives gallery (lane G exit walk; the area lanes' visual
// reference). Mounted by layouts/dashboard/index.jsx as a full-screen
// overlay only with `?oneview_gallery=1` in a prototype build.
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  Link,
  MenuList,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import Iconify from "src/components/iconify";
import DataTable from "src/components/data-table/DataTable";
import { useAgTheme } from "src/hooks/use-ag-theme";
import Term from "../Term";
import useColumnLayout from "./useColumnLayout";
import ColumnsMenu, { ColumnsPanel } from "./ColumnsMenu";
import { MenuRow, arrangeMenuItems } from "./MenuItems";
import { headerMenuItems } from "./headerMenuItems";
import useAgColumnShaping from "./useAgColumnShaping";
import useHoverSelection from "./useHoverSelection";
import Section, { useSections } from "./Section";
import ActionBar from "./ActionBar";
import ViewMenu from "./ViewMenu";
import ContextMenu, { useContextMenu } from "./ContextMenu";
import {
  copyIdItem,
  copyLinkItem,
  hideColumnItem,
  openTraceItem,
} from "./contextItems";
import Hint, { HintRecall } from "./Hint";
import InlineDetail from "./InlineDetail";
import HoverCard from "./HoverCard";
import OutlineToggle, { ExpandAllButton } from "./OutlineToggle";
import useOutline from "./useOutline";

const S = "gallery";

const CATALOG = [
  {
    id: "name",
    label: "Name",
    locked: true,
    defaultPinned: "left",
    group: "Dataset",
  },
  { id: "kind", label: "Kind", group: "Dataset", groupable: true },
  { id: "rows", label: "Rows", group: "Dataset" },
  { id: "version", label: "Version", group: "Dataset" },
  { id: "runs", label: "Runs", group: "Experiments" },
  { id: "best", label: "Best run", group: "Experiments" },
  { id: "updated", label: "Updated", group: "Activity" },
  { id: "owner", label: "Owner", group: "Activity", defaultVisible: false },
  { id: "tags", label: "Tags", group: "Activity", defaultVisible: false },
  {
    id: "cost",
    label: "Total cost",
    group: "Experiments",
    defaultVisible: false,
  },
  { id: "splits", label: "Splits", group: "Dataset", defaultVisible: false },
  {
    id: "id",
    label: "Dataset id",
    group: "Dataset",
    defaultVisible: false,
    pinnable: false,
  },
];

const ROWS = [
  [
    "Support agent · golden set",
    "Rows",
    248,
    "v6",
    12,
    "#4B 86.6%",
    "2h ago",
    "Ravi Menon",
  ],
  [
    "Support ops tasks · sandbox",
    "Task suite",
    30,
    "v2",
    7,
    "#2D 91.0%",
    "5m ago",
    "Maya Chen",
  ],
  ["Refund policy QA", "Rows", 75, "v3", 4, "#3A 79.4%", "4d ago", "Ana Ruiz"],
  [
    "Escalation transcripts",
    "Conversations",
    120,
    "v1",
    2,
    "#1A 64.0%",
    "Oct 12",
    "Ravi Menon",
  ],
  [
    "Billing edge cases",
    "Rows",
    52,
    "v4",
    5,
    "#2B 71.2%",
    "1d ago",
    "Maya Chen",
  ],
  [
    "Tool-use regressions",
    "Task suite",
    18,
    "v2",
    3,
    "#1C 55.6%",
    "3h ago",
    "Ana Ruiz",
  ],
].map((r, i) => ({
  id: `ds-${i + 1}`,
  name: r[0],
  kind: r[1],
  rows: r[2],
  version: r[3],
  runs: r[4],
  best: r[5],
  updated: r[6],
  owner: r[7],
  tags: i % 2 ? "golden" : "—",
  cost: `$${(0.42 * (i + 1)).toFixed(2)}`,
  splits: i % 3 ? "train · test" : "—",
}));

function Card({ title, note, children, sx }) {
  return (
    <Paper
      variant="outlined"
      sx={{ p: 2, borderRadius: 1.5, minWidth: 0, ...sx }}
    >
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        {title}
      </Typography>
      {note && (
        <Typography typography="s3" color="text.secondary" sx={{ mb: 1.25 }}>
          {note}
        </Typography>
      )}
      {children}
    </Paper>
  );
}

Card.propTypes = {
  title: PropTypes.node,
  note: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
};

const ACTIONS = (log) => [
  {
    id: "diff",
    label: "Diff",
    icon: "mdi:vector-difference",
    priority: 1,
    toggle: { on: true },
    shortcut: "diffToggle",
    run: () => log("Diff"),
  },
  {
    id: "review",
    label: "Review",
    icon: "mdi:account-check-outline",
    priority: 2,
    hideLabelBelow: 900,
    menu: [
      {
        id: "start",
        label: "Start here",
        icon: "mdi:play",
        run: () => log("Start here"),
      },
      {
        id: "queue",
        label: "Add to queue",
        icon: "mdi:tray-plus",
        run: () => log("Add to queue"),
      },
    ],
  },
  {
    id: "edit",
    label: "Edit",
    icon: "mdi:pencil-outline",
    priority: 3,
    run: () => log("Edit"),
  },
  {
    id: "csv",
    label: "Download CSV",
    icon: "mdi:download-outline",
    priority: 3,
    run: () => log("CSV"),
  },
  {
    id: "evals",
    label: "Add evaluations",
    icon: "mdi:check-decagram-outline",
    priority: 3,
    disabled: true,
    reason: "Viewers can't add evaluations",
  },
  {
    id: "run",
    label: "Run experiment",
    icon: "mdi:play-circle-outline",
    priority: 1,
    variant: "primary",
    run: () => log("Run"),
  },
];

/** The gallery overlay. */
export default function OneViewGallery() {
  const [log, setLog] = useState("");
  const say = (m) => setLog(`${m} · ${new Date().toLocaleTimeString()}`);
  const layout = useColumnLayout({
    surface: S,
    scopeId: "table",
    columns: CATALOG,
    commandLabel: "Columns of the gallery table…",
  });
  const menuLayout = useColumnLayout({
    surface: S,
    scopeId: "menus",
    columns: CATALOG,
    commands: false,
  });
  const agLayout = useColumnLayout({
    surface: S,
    scopeId: "ag",
    columns: CATALOG.slice(0, 8),
    commandLabel: "Columns of the AG grid…",
  });
  const [sorting, setSorting] = useState([{ id: "updated", desc: true }]);
  const [selection, setSelection] = useState({});
  const [density, setDensity] = useState("tall");
  const [costBars, setCostBars] = useState(true);
  const [why, setWhy] = useState(false);
  const [detailOpen, setDetailOpen] = useState(true);
  const outline = useOutline({
    surface: S,
    scopeId: "steps",
    ids: ["s1", "s2", "s3"],
    defaultOpen: (id) => id === "s2",
  });
  const sec = useSections(S, "page");
  const cm = useContextMenu();
  const gridRef = useRef(null);
  const agTheme = useAgTheme();
  const hover = useHoverSelection(gridRef);
  const shaping = useAgColumnShaping({ gridRef, layout: agLayout });
  const agDefs = useMemo(
    () =>
      CATALOG.slice(0, 8).map((c) => ({
        field: c.id,
        headerName: c.label,
        minWidth: 90,
        flex: c.id === "name" ? 2 : 1,
      })),
    [],
  );

  const columns = useMemo(
    () =>
      CATALOG.map((c) => ({
        id: c.id,
        accessorKey: c.id,
        header: c.label,
        size: c.id === "name" ? 240 : 120,
        enableSorting: c.id !== "tags",
      })),
    [],
  );

  const headerItems = arrangeMenuItems(
    headerMenuItems(menuLayout, "rows", {
      sort: { id: "rows", desc: true },
      onSort: () => {},
      group: null,
      onGroup: () => {},
    }),
  );

  const close = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("oneview_gallery");
    window.location.assign(url.toString());
  };

  return (
    <Box
      data-oneview-gallery
      sx={{
        position: "fixed",
        inset: 0,
        zIndex: 1250,
        overflow: "auto",
        bgcolor: "background.default",
        p: 3,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 2 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          One view primitives
        </Typography>
        <HintRecall id="gallery-first" />
        <Typography typography="s2" color="text.secondary" sx={{ flex: 1 }}>
          Lane G gallery · ONE-VIEW-SPEC §3 ·{" "}
          {log || "click things; the last action shows here"}
        </Typography>
        <Button size="small" onClick={sec.expandAll}>
          Expand all sections
        </Button>
        <Button size="small" onClick={sec.collapseAll}>
          Collapse all sections
        </Button>
        <IconButton aria-label="Close gallery" onClick={close}>
          <Iconify icon="mdi:close" />
        </IconButton>
      </Stack>

      <Hint id="gallery-first">
        Point at a row to select it, right-click a header for Sort, Freeze, Move
        and Hide, and use View to change what this page shows.
      </Hint>

      <Box
        sx={{
          mt: 2,
          display: "grid",
          gap: 2,
          gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
          alignItems: "start",
        }}
      >
        <Card
          title="ColumnsMenu (open)"
          note="Button + `3 hidden` chip; the panel: search, drag, show, freeze, lock."
        >
          <ColumnsMenu layout={menuLayout} />
          <Paper variant="outlined" sx={{ mt: 1, width: 322, borderRadius: 1 }}>
            <ColumnsPanel
              layout={menuLayout}
              groups={[
                { id: "Dataset", label: "Dataset" },
                { id: "Experiments", label: "Experiments" },
                { id: "Activity", label: "Activity" },
              ]}
            />
          </Paper>
        </Card>

        <Card
          title="HeaderMenu (open)"
          note="Right-click / ⋮ / Shift+F10 on a header. Disabled rows say why."
        >
          <Paper variant="outlined" sx={{ width: 260, borderRadius: 1 }}>
            <MenuList dense>
              {headerItems.map((it, i) =>
                it.divider ? (
                  <Divider key={`d${i}`} sx={{ my: "4px !important" }} />
                ) : (
                  <MenuRow
                    key={it.id}
                    item={it}
                    onRun={(x) => {
                      x.run?.();
                      say(x.label);
                    }}
                  />
                ),
              )}
            </MenuList>
          </Paper>
        </Card>

        <Card
          title="ViewMenu (open, modified)"
          note="Density · Columns · Show on this page · Saved views · Reset view."
        >
          <ViewMenu
            inline
            surface={S}
            scopeId="page"
            density={{ value: density, onChange: setDensity }}
            columns={menuLayout}
            extras={[
              {
                id: "costBars",
                label: "Cost bars",
                checked: costBars,
                onChange: setCostBars,
              },
              {
                id: "why",
                label: "Why column",
                checked: why,
                onChange: setWhy,
                description: "One line on why each row moved",
              },
            ]}
            savedViews={{
              state: { density },
              onApply: (s) => s.density && setDensity(s.density),
            }}
            isModified={density !== "comfortable" || !costBars || why}
            onReset={() => {
              setDensity("comfortable");
              setCostBars(true);
              setWhy(false);
            }}
          />
        </Card>

        <Card
          title="Section (open and collapsed)"
          note="A collapsed section still says its answer."
        >
          <Section
            id="charts"
            surface={S}
            scopeId="page"
            title="Charts"
            count={3}
            summary="Charts · best #2D 91.0% · cheapest #2A $0.0040 · fastest #1A 6.0 s"
            actions={
              <Button
                size="small"
                color="inherit"
                sx={{ typography: "s2", color: "text.secondary" }}
              >
                Metric ▾
              </Button>
            }
          >
            <Box
              sx={{
                height: 72,
                borderRadius: 1,
                bgcolor: "action.hover",
                display: "grid",
                placeItems: "center",
                typography: "s2",
                color: "text.secondary",
              }}
            >
              chart
            </Box>
          </Section>
          <Section
            id="frontier"
            surface={S}
            scopeId="page"
            title="Cost vs success"
            defaultOpen={false}
            summary="#2D is on the frontier; #2A is cheapest within 3 pts"
          >
            <Box
              sx={{ height: 72, borderRadius: 1, bgcolor: "action.hover" }}
            />
          </Section>
        </Card>

        <Card
          title="Hint (strip + footer) and InlineDetail"
          note="One tip per screen; × hides it for good; ⓘ recalls it."
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            <Box sx={{ p: 1.5 }}>
              <InlineDetail
                open={detailOpen}
                onToggle={() => setDetailOpen((o) => !o)}
                label="step 3 · lookup_order"
                summary={
                  <span>
                    <b>Step 3</b> · lookup_order got 404 for &quot;8830&quot;
                  </span>
                }
                copyValue={'{"order_id": "8830"}'}
                traceHref="/dashboard/observe"
              >
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    typography: "s3",
                    fontFamily: "monospace",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {
                    '{\n  "tool": "lookup_order",\n  "input": {"order_id": "8830"},\n  "error": "404 Not Found"\n}'
                  }
                </Box>
              </InlineDetail>
            </Box>
            <Hint id="gallery-footer" variant="footer">
              J / K next and previous row · E edit · X collapse all fields · ?
              all keys
            </Hint>
          </Box>
        </Card>

        <Card title="HoverCard, Term, OutlineToggle, ExpandAllButton">
          <Stack gap={1.25}>
            <Typography typography="s2">
              <HoverCard
                trigger={<b data-gallery-hover>#2D Claude Code</b>}
                title="#2D Claude Code × claude-sonnet-4-5"
                stats={[
                  { label: "Success", value: "91.0%" },
                  { label: "Cost per task", value: "$0.21" },
                  { label: "Time per task", value: "74 s" },
                ]}
                actions={
                  <Link href="#" typography="s2" underline="hover">
                    Open run
                  </Link>
                }
              />{" "}
              beats baseline <Term k="baseline">baseline</Term> #2B by +25.4{" "}
              <Term k="ci95">pts</Term>.
            </Typography>
            {["s1", "s2", "s3"].map((id, i) => (
              <Stack key={id} direction="row" alignItems="center" gap={1}>
                <OutlineToggle
                  open={outline.isOpen(id)}
                  onToggle={() => outline.toggle(id)}
                  label={`step ${i + 1}`}
                />
                <Typography typography="s2">
                  Step {i + 1} ·{" "}
                  {["classify_intent", "lookup_order", "respond"][i]}
                </Typography>
                {outline.isOpen(id) && (
                  <Typography typography="s3" color="text.secondary">
                    input, output, 412 ms
                  </Typography>
                )}
              </Stack>
            ))}
            <Box>
              <ExpandAllButton outline={outline} />
            </Box>
          </Stack>
        </Card>
      </Box>

      <Card
        title="ActionBar at 1280 / 1440 / 1920 toolbar widths"
        note="Lowest priority folds first; labels drop before an action folds; primary never folds; everything is in ⌘K."
        sx={{ mt: 2 }}
      >
        {[
          [380, "1280"],
          [600, "1440"],
          [1100, "1920"],
        ].map(([w, label]) => (
          <Stack
            key={w}
            direction="row"
            alignItems="center"
            gap={1.5}
            sx={{ mb: 1 }}
          >
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ width: 44 }}
            >
              {label}
            </Typography>
            <Box
              data-bar-width={w}
              sx={{
                width: w,
                border: "1px dashed",
                borderColor: "divider",
                borderRadius: 1,
                p: 0.5,
                display: "flex",
              }}
            >
              <ActionBar surface={`${S}-${label}`} actions={ACTIONS(say)} />
            </Box>
          </Stack>
        ))}
      </Card>

      <Card
        title="DataTable · layout + headerMenu + selectOnHover"
        note="Right-click a header; freeze Name/Rows and scroll sideways; point at a row to see its checkbox."
        sx={{ mt: 2 }}
      >
        <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
          <ColumnsMenu layout={layout} />
          <Box sx={{ flex: 1 }} />
          <Typography typography="s3" color="text.secondary">
            right-click a row for the context menu
          </Typography>
        </Stack>
        <Box
          sx={{ height: 320, width: 900, maxWidth: "100%" }}
          onContextMenu={(e) => {
            const rowEl = e.target.closest?.(".MuiDataGrid-row");
            if (!rowEl) return;
            const id = rowEl.getAttribute("data-id");
            cm.open(e, [
              {
                id: "open",
                label: "Open dataset",
                icon: "mdi:open-in-app",
                shortcut: "open",
                run: () => say(`Open ${id}`),
              },
              copyLinkItem(`/dashboard/develop/${id}`),
              copyIdItem(id, "dataset"),
              openTraceItem("/dashboard/observe"),
              hideColumnItem(layout, "kind"),
              {
                id: "delete",
                label: "Delete dataset",
                icon: "mdi:trash-can-outline",
                danger: true,
                run: () => say("Delete"),
              },
            ]);
            e.preventDefault();
          }}
        >
          <DataTable
            columns={columns}
            data={ROWS}
            rowCount={ROWS.length}
            sorting={sorting}
            onSortingChange={setSorting}
            enableSelection
            rowSelection={selection}
            onRowSelectionChange={setSelection}
            layout={layout}
            headerMenu
            selectOnHover
            ariaLabel="Gallery datasets"
          />
        </Box>
        <ContextMenu {...cm.menuProps} />
      </Card>

      <Card
        title="AG Grid · useAgColumnShaping + hover selection"
        note="AG's own column menu is our menu (header ⋮ and right-click); drag / pin / resize write back to the layout."
        sx={{ mt: 2, mb: 6 }}
      >
        <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
          <ColumnsMenu layout={agLayout} buttonVariant="icon" />
          <Typography typography="s3" color="text.secondary" data-ag-visible>
            {agLayout.visibleIds.join(" · ")}
          </Typography>
        </Stack>
        <Box
          className={hover.className}
          sx={{ height: 260, width: 900, maxWidth: "100%" }}
        >
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            rowData={ROWS}
            columnDefs={agDefs}
            rowSelection={{ mode: "multiRow" }}
            selectionColumnDef={{ pinned: "left", width: 44 }}
            getRowId={(p) => p.data.id}
            rowHeight={36}
            headerHeight={36}
            {...shaping}
            onSelectionChanged={hover.onSelectionChanged}
          />
        </Box>
      </Card>
    </Box>
  );
}
