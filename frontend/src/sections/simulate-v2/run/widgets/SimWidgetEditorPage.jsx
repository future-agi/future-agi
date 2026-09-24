import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Box, Breadcrumbs, Button, Checkbox, Chip, Divider, FormControl, IconButton,
  InputAdornment, Link, ListItemIcon, ListItemText, Menu, MenuItem, Select, Stack,
  Tab, Tabs, TextField, Tooltip, Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import WidgetDescriptionPopover from "src/sections/dashboards/WidgetDescriptionPopover";
import TruncatedTooltipText from "src/sections/dashboards/TruncatedTooltipText";
import { ALL_AGGREGATIONS } from "src/sections/dashboards/constants";
import {
  formatValueWithConfig,
  getAggColumnLabel,
  getSeriesScalar,
  getUnitRendering,
} from "src/sections/dashboards/widgetUtils";
import {
  AggregationPicker,
  CHART_TYPES,
  ChartAxisSettings,
  DATASET_EXTRA_AGGREGATIONS,
  LETTER_LABELS,
  METRIC_TYPE_ICONS,
  PresetPillGroup,
  ViewModeBar,
  defaultAxisConfig,
  defaultLeftYAxis,
  defaultRightYAxis,
} from "src/sections/dashboards/widgetEditorParts";
import { useSimStore, useEnvState } from "../../store";
import { getEnvironment } from "../../_mock/environments";
import { useRunLayout } from "../layout/useRunLayout";
import { PANELS } from "../layout/panelRegistry";
import SimWidgetChart, { chartKindOf, useSeriesColors } from "./SimWidgetChart";
import SimQueryPicker, { SimFilterValuePicker } from "./SimQueryPicker";
import { builtinQuery } from "./builtinQueries";
import {
  RUN_RANGE_PRESETS,
  SIM_ATTRIBUTES,
  SIM_ATTRIBUTE_CATEGORIES,
  SIM_FILTER_OPERATORS,
  SIM_METRIC_CATEGORIES,
  X_AXIS_OPTIONS,
  attributeAxisValue,
  distinctAttributeValues,
  metricFromCatalog,
  newSimQuery,
  runSimQuery,
  runsInRange,
  simMetricCatalog,
  simRunsWithTasks,
  topSeriesKeys,
  MAX_DEFAULT_SERIES,
} from "./simWidgetQuery";

/**
 * Run-analytics widget editor — the Observe dashboards widget editor, on
 * simulation data. Same header (breadcrumb · inline name · description · ⋯ ·
 * Close · Save), same controls row, same chart / splitter / series table,
 * and the same Query + Chart panel. The shared pieces are imported from the
 * dashboards module; the query runs in the browser over this environment's
 * runs, with runs as the x-axis.
 */

const MAX_METRICS = 5;
const SAVED_NAV_DELAY_MS = 400;

const SECTION_HOVER_SX = {
  cursor: "pointer",
  borderRadius: 1,
  px: 1,
  py: 0.5,
  mx: -1,
  transition: "background-color 0.15s",
  "&:hover": {
    bgcolor: (t) => (t.palette.mode === "dark" ? "rgba(145, 107, 255, 0.12)" : "rgba(105, 65, 198, 0.08)"),
    "& .section-title": { color: "primary.main" },
  },
};

function SectionHeader({ title, required, onClick, disabled, tip }) {
  return (
    <Tooltip title={tip} placement="left" arrow>
      <Stack
        direction="row" justifyContent="space-between" alignItems="center"
        onClick={disabled ? undefined : onClick}
        sx={{ ...SECTION_HOVER_SX, ...(disabled && { cursor: "default" }) }}
      >
        <Typography className="section-title" variant="body2" fontWeight="fontWeightSemiBold" sx={{ transition: "color 0.15s" }}>
          {title}
          {required && <Typography component="span" color="error.main">*</Typography>}
        </Typography>
        <Iconify icon="mdi:plus" width={18} sx={{ color: disabled ? "text.disabled" : "text.secondary" }} />
      </Stack>
    </Tooltip>
  );
}

SectionHeader.propTypes = {
  title: PropTypes.string.isRequired,
  required: PropTypes.bool,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  tip: PropTypes.string,
};

const xAxisLabel = (v) => {
  const opt = X_AXIS_OPTIONS.find((o) => o.value === v);
  if (opt) return opt.label;
  const attr = String(v).startsWith("attr:") ? SIM_ATTRIBUTES.find((a) => `attr:${a.id}` === v) : null;
  return attr ? `By ${attr.name.toLowerCase()}` : v;
};

const valueLabel = (value) => {
  if (!Array.isArray(value) || !value.length) return "Select values";
  if (value.length <= 2) return value.join(", ");
  return `${value.slice(0, 2).join(", ")} +${value.length - 2}`;
};

export default function SimWidgetEditorPage() {
  const { envId, runId, widgetId } = useParams();
  const navigate = useNavigate();
  const theme = useTheme();
  const { state } = useSimStore();
  const { envState } = useEnvState(envId);
  const env = getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);
  const layout = useRunLayout({ surface: env?.surface || "generic" });

  const runs = useMemo(() => simRunsWithTasks(env, envState), [env, envState]);
  const catalog = useMemo(() => simMetricCatalog(runs), [runs]);
  const currentRun = runs.find((r) => r.id === runId) || runs[runs.length - 1];
  const analyticsUrl = `${paths.dashboard.simulate.simulationRun(envId, runId)}?tab=analytics`;

  const isNew = widgetId === "new";
  const customWidget = layout.customWidgets.find((w) => w.id === widgetId);
  const builtinPanel = !isNew && !customWidget ? PANELS.find((p) => p.id === widgetId) : null;

  /* ── editor state ── */
  const [initialized, setInitialized] = useState(false);
  const [approxNote, setApproxNote] = useState(null);
  /* A widget's own colours (a built-in opens with the colours it has on the
     Analytics tab) — kept through edits and saved with the widget. */
  const [display, setDisplay] = useState(null);
  const [chartName, setChartName] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [chartDescription, setChartDescription] = useState("");
  const [descOpen, setDescOpen] = useState(false);
  const descSlotRef = useRef(null);
  const [chartType, setChartType] = useState("line");
  const [query, setQuery] = useState(newSimQuery);
  const [axisConfig, setAxisConfig] = useState(defaultAxisConfig);
  const [visibleSeries, setVisibleSeries] = useState(null);
  const [viewMode, setViewMode] = useState("split-chart");
  const [chartHeight, setChartHeight] = useState(350);
  const [isDragging, setIsDragging] = useState(false);
  const [rightTab, setRightTab] = useState(0);
  const [tableSearch, setTableSearch] = useState("");
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [saveStatus, setSaveStatus] = useState("idle");
  const [picker, setPicker] = useState({ open: false, anchor: null, mode: "metric", index: null, metricIndex: null });
  const [valuePicker, setValuePicker] = useState({ anchor: null, scope: null, index: null, metricIndex: null });

  /* Load whatever this widget is: a saved custom widget, an edited or shipped
     built-in, or a blank new one. */
  useEffect(() => {
    /* Wait for the run history: the metric catalogue (eval metrics in
       particular) is built from it, and a built-in's query reads it. */
    if (initialized || !env || !runs.length) return;
    let saved = null;
    if (customWidget?.kind === "sim_query") saved = customWidget;
    else if (builtinPanel) {
      const override = layout.getOverride(builtinPanel.id);
      if (override?.simQuery) {
        saved = { title: override.title || builtinPanel.title, ...override.simQuery };
      } else {
        const q = builtinQuery(builtinPanel.id, catalog) || {};
        saved = { title: builtinPanel.title, ...q };
        setApproxNote(q.approx || null); // null for every built-in that maps exactly
      }
    }
    if (saved) {
      setChartName(saved.title || "");
      setChartDescription(saved.description || "");
      setChartType(saved.chart_type || "line");
      setQuery({ ...newSimQuery(), ...(saved.query || {}) });
      if (saved.axis_config) {
        const d = defaultAxisConfig();
        const a = saved.axis_config;
        setAxisConfig({
          ...d, ...a,
          leftY: { ...d.leftY, ...(a.leftY || {}) },
          rightY: { ...d.rightY, ...(a.rightY || {}) },
          xAxis: { ...d.xAxis, ...(a.xAxis || {}) },
          seriesAxis: a.seriesAxis || {},
        });
      }
      if (saved.display) setDisplay(saved.display);
      if (Array.isArray(saved.visible_series)) setVisibleSeries(new Set(saved.visible_series));
    }
    setInitialized(true);
  }, [initialized, env, runs.length, customWidget, builtinPanel, layout, catalog]);

  const result = useMemo(() => runSimQuery(query, runs, currentRun?.id, catalog), [query, runs, currentRun, catalog]);
  /* Runs a range can reach: every run up to and including the one open. */
  const availableRuns = useMemo(() => runsInRange(runs, "all", runId).length, [runs, runId]);
  const previewSeries = result.series;
  const buckets = result.buckets;
  const colorFor = useSeriesColors(previewSeries, display);

  /* Same rule as the dashboards editor: past ten series, start with the top
     ten ticked so the chart stays readable; the rest are one click away in
     the table. Re-applied when the series set changes (new breakdown). */
  const seriesSig = previewSeries.map((s) => s.key).join("\u0001");
  const lastSigRef = useRef(null);
  useEffect(() => {
    if (!initialized || lastSigRef.current === seriesSig) return;
    const firstLoad = lastSigRef.current === null;
    lastSigRef.current = seriesSig;
    if (firstLoad && visibleSeries) return; // keep a saved selection
    /* Tables and metric cards show every series; only plotted charts are
       trimmed to the top ten. */
    if (chartType === "table" || chartType === "metric") { setVisibleSeries(null); return; }
    if (previewSeries.length > MAX_DEFAULT_SERIES) setVisibleSeries(new Set(topSeriesKeys(previewSeries)));
    else setVisibleSeries(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesSig, initialized]);
  const visibleKeys = visibleSeries;

  const kind = chartKindOf(chartType);
  const isPie = chartType === "pie";
  const isTable = chartType === "table";
  const isMetricCard = chartType === "metric";
  const isHorizontal = chartType === "bar" || chartType === "stacked_bar";
  const hasBreakdown = query.breakdowns.length > 0;
  const showSplit = previewSeries.length > 0 && !isMetricCard && !isHorizontal && !isTable && !isPie;

  const setQ = (patch) => setQuery((q) => ({ ...q, ...patch }));
  const updateAxis = (axis, key, val) => setAxisConfig((prev) => ({ ...prev, [axis]: { ...prev[axis], [key]: val } }));
  const setSeriesAxis = (si, side) => setAxisConfig((prev) => {
    const seriesAxis = { ...prev.seriesAxis, [si]: side };
    return { ...prev, seriesAxis, rightY: { ...prev.rightY, visible: Object.values(seriesAxis).includes("right") } };
  });

  /* ── splitter drag ── */
  const dragStart = useRef(null);
  const handleDragStart = useCallback((e) => {
    e.preventDefault();
    dragStart.current = { y: e.clientY, h: chartHeight };
    setIsDragging(true);
    const move = (ev) => {
      const next = Math.max(0, Math.min(700, dragStart.current.h + (ev.clientY - dragStart.current.y)));
      setChartHeight(next);
      setViewMode(next === 0 ? "table" : next >= 600 ? "chart" : next >= 300 ? "split-chart" : "split-table");
    };
    const up = () => {
      setIsDragging(false);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }, [chartHeight]);

  /* ── picker ── */
  const openPicker = (e, mode, index = null, metricIndex = null) => setPicker({ open: true, anchor: e.currentTarget, mode, index, metricIndex });
  const closePicker = () => setPicker((p) => ({ ...p, open: false }));
  const pickerItems = useMemo(() => {
    if (picker.mode === "metric") {
      return catalog.map((c) => ({ ...c, type: c.type || "system" }));
    }
    /* Tool attributes (tool name / status) only exist on tool-call rows, so
       they are offered only when a tool metric is on the widget. */
    const hasToolMetric = query.metrics.some((m) => catalog.find((c) => c.id === m.id)?.grain === "tool");
    return SIM_ATTRIBUTES.map((a) => ({
      ...a,
      type: "custom_attribute",
      disabled: a.grain === "tool" && !hasToolMetric,
    }));
  }, [picker.mode, catalog, query.metrics]);

  const handlePickerSelect = (opt) => {
    const { mode, index, metricIndex } = picker;
    if (mode === "metric") {
      const def = catalog.find((c) => c.id === opt.id);
      const next = metricFromCatalog(def);
      const metrics = [...query.metrics];
      if (index != null) metrics[index] = { ...next, filters: metrics[index]?.filters || [] };
      else metrics.push(next);
      setQ({ metrics });
    } else if (mode === "breakdown") {
      const breakdowns = [...query.breakdowns];
      if (index != null) breakdowns[index] = { id: opt.id, name: opt.name };
      else breakdowns.splice(0, breakdowns.length, { id: opt.id, name: opt.name });
      setQ({ breakdowns });
      setVisibleSeries(null);
    } else if (mode === "filter") {
      const filters = [...query.filters];
      const f = { id: opt.id, name: opt.name, operator: "is", value: [] };
      if (index != null) filters[index] = f; else filters.push(f);
      setQ({ filters });
    } else if (mode === "metric_filter") {
      const metrics = [...query.metrics];
      const m = { ...metrics[metricIndex] };
      const mf = [...(m.filters || [])];
      const f = { id: opt.id, name: opt.name, operator: "is", value: [] };
      if (index != null) mf[index] = f; else mf.push(f);
      m.filters = mf;
      metrics[metricIndex] = m;
      setQ({ metrics });
    }
    closePicker();
  };

  const updateFilter = (scope, i, patch, metricIndex) => {
    if (scope === "metric") {
      const metrics = [...query.metrics];
      const m = { ...metrics[metricIndex] };
      m.filters = m.filters.map((f, fi) => (fi === i ? { ...f, ...patch } : f));
      metrics[metricIndex] = m;
      setQ({ metrics });
    } else {
      setQ({ filters: query.filters.map((f, fi) => (fi === i ? { ...f, ...patch } : f)) });
    }
  };
  const activeValueFilter = valuePicker.scope === "metric"
    ? query.metrics[valuePicker.metricIndex]?.filters?.[valuePicker.index]
    : valuePicker.scope === "global" ? query.filters[valuePicker.index] : null;

  /* ── save / delete / duplicate ── */
  const snapshot = () => ({
    title: chartName.trim() || "Untitled widget",
    description: chartDescription.trim(),
    chart_type: chartType,
    query,
    axis_config: axisConfig,
    display,
    visible_series: visibleSeries ? [...visibleSeries] : null,
  });

  const handleSave = () => {
    if (!query.metrics.length) return;
    setSaveStatus("saving");
    const snap = snapshot();
    if (builtinPanel) {
      layout.patchOverride(builtinPanel.id, { title: snap.title, simQuery: snap });
    } else if (customWidget) {
      layout.updateCustomWidget(customWidget.id, { kind: "sim_query", ...snap });
    } else {
      layout.addCustomWidget({ id: `sim-${Math.random().toString(36).slice(2, 10)}`, kind: "sim_query", ...snap });
    }
    setSaveStatus("saved");
    setTimeout(() => navigate(analyticsUrl), SAVED_NAV_DELAY_MS);
  };

  const handleDelete = () => {
    if (customWidget) layout.removeCustomWidget(customWidget.id);
    else if (builtinPanel) layout.hide(builtinPanel.id);
    navigate(analyticsUrl);
  };

  const handleDuplicate = () => {
    layout.addCustomWidget({
      id: `sim-${Math.random().toString(36).slice(2, 10)}`,
      kind: "sim_query",
      ...snapshot(),
      title: `${chartName || "Untitled widget"} (copy)`,
    });
  };

  const aggColumnLabel = getAggColumnLabel(query.metrics, [...ALL_AGGREGATIONS, ...DATASET_EXTRA_AGGREGATIONS]);
  const csvRows = () => [
    ["Metric", aggColumnLabel, ...buckets.map((b) => b.label)],
    ...previewSeries.map((s) => {
      const agg = s.total ?? getSeriesScalar(s.data, s.aggregation);
      return [s.name, agg == null ? "—" : agg.toFixed(2), ...s.data.map((pt) => (pt.y != null ? pt.y : ""))];
    }),
  ];
  const toCsv = () => csvRows().map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");

  if (!env) {
    return (
      <Box sx={{ p: 4 }}>
        <Typography>Environment not found.</Typography>
      </Box>
    );
  }

  const trimmedDescription = chartDescription.trim();
  /* Same label as the run page header (the run's own number), not the
     position on the runs axis. */
  const runLabel = currentRun
    ? `Run ${currentRun.storedOrdinal ?? currentRun.ordinal ?? ""} · agent ${currentRun.agentVersion}`
    : "Run";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", bgcolor: "background.paper" }}>
      {/* ── Header ── */}
      <Stack direction="row" alignItems="center" sx={{ px: 2, py: 1, borderBottom: `1px solid ${theme.palette.divider}`, gap: 1, minHeight: 48 }}>
        <Breadcrumbs separator={<Iconify icon="mdi:chevron-right" width={14} />} sx={{ flexShrink: 0 }}>
          <Link underline="hover" color="text.secondary" sx={{ cursor: "pointer", fontSize: "13px" }} onClick={() => navigate(paths.dashboard.simulate.environmentStep(envId, "runs"))}>
            {env.name}
          </Link>
          <Link underline="hover" color="text.secondary" sx={{ cursor: "pointer", fontSize: "13px", whiteSpace: "nowrap" }} onClick={() => navigate(analyticsUrl)}>
            {runLabel} · Analytics
          </Link>
        </Breadcrumbs>
        <Iconify icon="mdi:chevron-right" width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />

        {editingName ? (
          <TextField
            value={chartName}
            onChange={(e) => setChartName(e.target.value)}
            onBlur={() => setEditingName(false)}
            onKeyDown={(e) => { if (e.key === "Enter") setEditingName(false); }}
            autoFocus size="small" variant="outlined" placeholder="Untitled widget"
            sx={{ minWidth: 200, maxWidth: 350, "& .MuiOutlinedInput-input": { py: 0.5, fontSize: "14px", fontWeight: 500 } }}
          />
        ) : (
          <Typography
            onClick={() => setEditingName(true)}
            sx={{ fontSize: "14px", fontWeight: 500, cursor: "pointer", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", "&:hover": { color: "primary.main" } }}
          >
            {chartName || "Untitled widget"}
          </Typography>
        )}

        <Box ref={descSlotRef} sx={{ display: "flex", minWidth: 0 }}>
          {trimmedDescription ? (
            <TruncatedTooltipText text={trimmedDescription}>
              {(measureRef) => (
                <Stack
                  direction="row" alignItems="center" gap={0.5} role="button" tabIndex={0}
                  onClick={() => setDescOpen(true)}
                  sx={{ minWidth: 0, maxWidth: 260, px: 0.75, py: 0.25, borderRadius: 1, color: "text.secondary", cursor: "pointer", "&:hover": { bgcolor: "action.hover", color: "text.primary" } }}
                >
                  <Iconify icon="mdi:text-box-outline" width={14} sx={{ flexShrink: 0 }} />
                  <Typography ref={measureRef} noWrap sx={{ fontSize: "13px", minWidth: 0 }}>{trimmedDescription}</Typography>
                </Stack>
              )}
            </TruncatedTooltipText>
          ) : (
            <Button
              size="small" startIcon={<Iconify icon="mdi:plus" width={14} />} onClick={() => setDescOpen(true)}
              sx={{ flexShrink: 0, fontSize: "13px", fontWeight: 400, color: "text.disabled", "&:hover": { color: "text.secondary" } }}
            >
              Add description
            </Button>
          )}
        </Box>
        <WidgetDescriptionPopover
          open={descOpen}
          anchorEl={descSlotRef.current}
          value={chartDescription}
          onChange={setChartDescription}
          onClose={() => setDescOpen(false)}
        />

        <Box sx={{ flex: 1 }} />
        <IconButton size="small" aria-label="More actions" onClick={(e) => setMoreAnchor(e.currentTarget)} sx={{ color: "text.secondary" }}>
          <Iconify icon="mdi:dots-horizontal" width={20} />
        </IconButton>
        <Menu
          anchorEl={moreAnchor} open={!!moreAnchor} onClose={() => setMoreAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 180 } } }}
        >
          {!isNew && (
            <MenuItem onClick={() => { setMoreAnchor(null); setConfirmDelete(true); }} sx={{ color: "error.main" }}>
              <ListItemIcon><Iconify icon="mdi:delete-outline" width={18} sx={{ color: "error.main" }} /></ListItemIcon>
              <ListItemText>{builtinPanel ? "Hide" : "Delete"}</ListItemText>
            </MenuItem>
          )}
          <MenuItem onClick={() => { setMoreAnchor(null); setTimeout(() => setEditingName(true), 150); }}>
            <ListItemIcon><Iconify icon="mdi:pencil-outline" width={18} /></ListItemIcon>
            <ListItemText>Rename</ListItemText>
          </MenuItem>
          <MenuItem disabled={!query.metrics.length} onClick={() => { setMoreAnchor(null); handleDuplicate(); }}>
            <ListItemIcon><Iconify icon="mdi:content-copy" width={18} /></ListItemIcon>
            <ListItemText>Duplicate</ListItemText>
          </MenuItem>
          <Divider />
          <MenuItem
            disabled={!previewSeries.length}
            onClick={() => {
              setMoreAnchor(null);
              const url = URL.createObjectURL(new Blob([toCsv()], { type: "text/csv" }));
              const a = document.createElement("a");
              a.href = url;
              a.download = `${chartName || "widget"}.csv`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            <ListItemIcon><Iconify icon="mdi:download-outline" width={18} /></ListItemIcon>
            <ListItemText>Export CSV</ListItemText>
          </MenuItem>
          <MenuItem disabled={!previewSeries.length} onClick={() => { setMoreAnchor(null); navigator.clipboard?.writeText(toCsv()); }}>
            <ListItemIcon><Iconify icon="mdi:content-copy" width={18} /></ListItemIcon>
            <ListItemText>Copy CSV</ListItemText>
          </MenuItem>
          {builtinPanel && layout.getOverride(builtinPanel.id)?.simQuery && (
            <MenuItem onClick={() => { setMoreAnchor(null); layout.resetOverride(builtinPanel.id); navigate(analyticsUrl); }}>
              <ListItemIcon><Iconify icon="mdi:restore" width={18} /></ListItemIcon>
              <ListItemText>Reset to default</ListItemText>
            </MenuItem>
          )}
        </Menu>
        <ConfirmDialog
          open={confirmDelete}
          onClose={() => setConfirmDelete(false)}
          title={builtinPanel ? "Hide Widget" : "Delete Widget"}
          content={builtinPanel
            ? `Hide "${chartName || "this widget"}" from the analytics tab? You can bring it back from Hidden.`
            : `Are you sure you want to delete "${chartName || "this widget"}"? This action cannot be undone.`}
          action={(
            <Button variant="contained" color="error" size="small" onClick={handleDelete}>
              {builtinPanel ? "Hide" : "Delete"}
            </Button>
          )}
        />
        <Button onClick={() => navigate(analyticsUrl)} sx={{ color: "text.primary", fontWeight: 500 }}>Close</Button>
        <Tooltip title={query.metrics.length ? "" : "Add at least one metric"}>
          <span>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={!query.metrics.length || saveStatus !== "idle"}
              color={saveStatus === "saved" ? "success" : "primary"}
              startIcon={saveStatus === "saved" ? <Iconify icon="mdi:check" width={18} /> : undefined}
            >
              {saveStatus === "saving" ? "Saving..." : saveStatus === "saved" ? "Saved" : "Save"}
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {/* ── Main ── */}
      <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Left: chart area */}
        <Box sx={{ flex: 1, p: 2, display: "flex", flexDirection: "column", gap: 2, overflow: "auto", minWidth: 0 }}>
          <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
            {/* A range can only reach back as far as the runs that exist up to
                this one: "Last 10 runs" with 3 runs is greyed out, and the
                counts say how many runs each option actually reads. */}
            <PresetPillGroup
              options={RUN_RANGE_PRESETS}
              value={query.range}
              onSelect={(range) => setQ({ range })}
              theme={theme}
              isDisabled={(v) => v !== "this" && v !== "all" && Number(v) > availableRuns}
              titleFor={(v) => (v !== "this" && v !== "all" && Number(v) > availableRuns
                ? `Only ${availableRuns} run${availableRuns === 1 ? "" : "s"} so far`
                : undefined)}
              renderLabel={(p) => (p.value === "all" ? `All runs (${availableRuns})` : p.label)}
            />
            <Box sx={{ flex: 1, minWidth: 0 }} />
            <FormControl size="small" sx={{ minWidth: 80 }}>
              <Select
                value={query.xAxis}
                onChange={(e) => setQ({ xAxis: e.target.value })}
                sx={{ fontSize: "13px", "& .MuiSelect-select": { py: 0.7 } }}
                renderValue={(v) => xAxisLabel(v)}
                MenuProps={{ PaperProps: { sx: { maxHeight: 420 } } }}
              >
                {X_AXIS_OPTIONS.map((g) => <MenuItem key={g.value} value={g.value}>{g.label}</MenuItem>)}
                <Divider />
                {SIM_ATTRIBUTES.map((a) => (
                  <MenuItem key={a.id} value={attributeAxisValue(a.id)}>By {a.name.toLowerCase()}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 120 }}>
              <Select
                value={chartType}
                onChange={(e) => setChartType(e.target.value)}
                sx={{ fontSize: "13px", "& .MuiSelect-select": { py: 0.7 } }}
                renderValue={(val) => {
                  const ct = CHART_TYPES.find((t) => t.value === val);
                  return (
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      <Iconify icon={ct?.icon || "mdi:chart-line"} width={16} />
                      {ct?.label || val}
                    </Stack>
                  );
                }}
              >
                {CHART_TYPES.flatMap((ct, i) => {
                  const prev = i > 0 ? CHART_TYPES[i - 1] : null;
                  const pieDisabled = ct.value === "pie" && !hasBreakdown;
                  return [
                    prev && prev.group !== ct.group && <Divider key={`div-${i}`} />,
                    <MenuItem key={ct.value} value={ct.value} disabled={pieDisabled}>
                      <Stack direction="row" alignItems="center" gap={0.5}>
                        <Iconify icon={ct.icon} width={16} />
                        {ct.label}
                        {pieDisabled && <Typography variant="caption" color="text.disabled" sx={{ ml: 1 }}>add a breakdown</Typography>}
                      </Stack>
                    </MenuItem>,
                  ].filter(Boolean);
                })}
              </Select>
            </FormControl>
          </Stack>

          {approxNote && (
            <Stack
              direction="row" alignItems="flex-start" gap={1}
              sx={{ px: 1.5, py: 1, borderRadius: 1, border: `1px solid ${theme.palette.divider}`, bgcolor: "action.hover" }}
            >
              <Iconify icon="mdi:information-outline" width={16} sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }} />
              <Typography variant="body2" sx={{ fontSize: "13px", color: "text.secondary" }}>
                Closest query to the built-in chart. {approxNote} Saving replaces the built-in with this widget; Reset to default brings it back.
              </Typography>
            </Stack>
          )}

          {/* Chart + view toggles + series table */}
          <Box sx={{ flex: 1, minHeight: 420, border: `1px solid ${theme.palette.divider}`, borderRadius: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {showSplit ? (
              <>
                {chartHeight > 0 && (
                  <Box sx={{ height: chartHeight, flexShrink: 0, p: 1.5, pb: 0 }}>
                    <SimWidgetChart series={previewSeries} buckets={buckets} chartType={chartType} axisConfig={axisConfig} visible={visibleKeys} display={display} />
                  </Box>
                )}
                <ViewModeBar
                  viewMode={viewMode}
                  onSelect={(mode, height) => { setViewMode(mode); setChartHeight(height); }}
                  onDragStart={handleDragStart}
                  isDragging={isDragging}
                  theme={theme}
                />
                {viewMode !== "chart" && (
                  <SeriesTable
                    series={previewSeries}
                    buckets={buckets}
                    visible={visibleSeries}
                    setVisible={setVisibleSeries}
                    search={tableSearch}
                    setSearch={setTableSearch}
                    colorFor={colorFor}
                    aggColumnLabel={aggColumnLabel}
                    leftY={axisConfig.leftY}
                    theme={theme}
                  />
                )}
              </>
            ) : (
              <Box sx={{ flex: 1, p: 1.5, minHeight: 0 }}>
                <SimWidgetChart series={previewSeries} buckets={buckets} chartType={chartType} axisConfig={axisConfig} visible={visibleKeys} display={display} />
              </Box>
            )}
            {isPie && previewSeries.length > 0 && (
              <Box sx={{ display: "flex", borderTop: `1px solid ${theme.palette.divider}` }}>
                {query.metrics.map((m, i) => (
                  <Box key={i} sx={{ flex: 1, textAlign: "center", py: 2, px: 1.5, borderRight: i < query.metrics.length - 1 ? `1px solid ${theme.palette.divider}` : "none" }}>
                    <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "13px" }}>{LETTER_LABELS[i]} {m.name}</Typography>
                    <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "11px", display: "block" }}>
                      {m.aggregation} · {previewSeries.filter((s) => s.metricIndex === i).length} slices
                    </Typography>
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        </Box>

        {/* Right: Query / Chart */}
        <Box sx={{ width: 320, minWidth: 320, borderLeft: `1px solid ${theme.palette.divider}`, display: "flex", flexDirection: "column", overflow: "auto" }}>
          <Tabs value={rightTab} onChange={(_, v) => setRightTab(v)} sx={{ px: 2, minHeight: 40 }}>
            <Tab label="Query" sx={{ minHeight: 40, textTransform: "none" }} />
            <Tab label="Chart" sx={{ minHeight: 40, textTransform: "none" }} />
          </Tabs>
          <Divider />
          {rightTab === 0 && (
            <Box sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
              {/* Metric */}
              <Box>
                <SectionHeader
                  title="Metric" required
                  tip="Choose what to measure and track."
                  disabled={query.metrics.length >= MAX_METRICS}
                  onClick={(e) => openPicker(e, "metric")}
                />
                {query.metrics.map((m, i) => {
                  const def = catalog.find((c) => c.id === m.id);
                  return (
                    <Box key={i} sx={{ mt: 1, p: 1.5, border: `1px solid ${theme.palette.divider}`, borderRadius: 1, "&:hover .metric-hover-action": { opacity: 1 } }}>
                      <Stack direction="row" alignItems="center" gap={1}>
                        <Chip
                          label={LETTER_LABELS[i]} size="small" variant="outlined"
                          sx={{ minWidth: 24, height: 24, fontSize: "12px", fontWeight: 600, "& .MuiChip-label": { px: "0px !important" } }}
                        />
                        <Iconify icon={METRIC_TYPE_ICONS[m.type] || "mdi:cog-outline"} width={16} sx={{ color: "text.secondary" }} />
                        <Typography
                          variant="body2" noWrap title={m.name}
                          sx={{ flex: 1, cursor: "pointer", maxWidth: 160, "&:hover": { color: "primary.main" } }}
                          onClick={(e) => openPicker(e, "metric", i)}
                        >
                          {m.name}
                        </Typography>
                        <Tooltip title="Add filter to this metric">
                          <IconButton
                            className="metric-hover-action" size="small"
                            onClick={(e) => openPicker(e, "metric_filter", null, i)}
                            sx={{ opacity: m.filters?.length ? 1 : 0, transition: "opacity 0.15s", color: m.filters?.length ? "primary.main" : "text.secondary" }}
                          >
                            <Iconify icon="mdi:filter-outline" width={16} />
                          </IconButton>
                        </Tooltip>
                        <IconButton
                          className="metric-hover-action" size="small" aria-label={`Remove ${m.name}`}
                          onClick={() => { setQ({ metrics: query.metrics.filter((_, j) => j !== i) }); setVisibleSeries(null); }}
                          sx={{ opacity: 0, transition: "opacity 0.15s" }}
                        >
                          <Iconify icon="mdi:close" width={14} />
                        </IconButton>
                      </Stack>
                      <AggregationPicker
                        value={m.aggregation}
                        onChange={(val) => setQ({ metrics: query.metrics.map((x, j) => (j === i ? { ...x, aggregation: val } : x)) })}
                        theme={theme}
                        allowedAggregations={def?.allowed || m.allowedAggregations || undefined}
                        extraOptions={def?.category === "evals" ? DATASET_EXTRA_AGGREGATIONS : undefined}
                      />
                      {(m.filters || []).map((mf, fi) => (
                        <FilterRow
                          key={fi} filter={mf} compact
                          onPickAttr={(e) => openPicker(e, "metric_filter", fi, i)}
                          onRemove={() => {
                            const metrics = [...query.metrics];
                            metrics[i] = { ...m, filters: m.filters.filter((_, k) => k !== fi) };
                            setQ({ metrics });
                          }}
                          onOperator={(operator) => updateFilter("metric", fi, { operator }, i)}
                          onPickValues={(e) => setValuePicker({ anchor: e.currentTarget, scope: "metric", index: fi, metricIndex: i })}
                          theme={theme}
                        />
                      ))}
                    </Box>
                  );
                })}
              </Box>

              {/* Filter */}
              <Box>
                <SectionHeader title="Filter" tip="Filter to include or exclude specific tasks." onClick={(e) => openPicker(e, "filter")} />
                {query.filters.map((f, i) => (
                  <FilterRow
                    key={i} filter={f}
                    onPickAttr={(e) => openPicker(e, "filter", i)}
                    onRemove={() => setQ({ filters: query.filters.filter((_, j) => j !== i) })}
                    onOperator={(operator) => updateFilter("global", i, { operator })}
                    onPickValues={(e) => setValuePicker({ anchor: e.currentTarget, scope: "global", index: i, metricIndex: null })}
                    theme={theme}
                  />
                ))}
              </Box>

              {/* Breakdown */}
              <Box>
                <SectionHeader
                  title="Breakdown"
                  tip="Split each metric into one series per value."
                  disabled={query.breakdowns.length >= 1}
                  onClick={(e) => openPicker(e, "breakdown")}
                />
                {query.breakdowns.map((b, i) => (
                  <Box key={i} sx={{ mt: 1, p: 1.5, border: `1px solid ${theme.palette.divider}`, borderRadius: 1, "&:hover .breakdown-hover-action": { opacity: 1 } }}>
                    <Stack direction="row" alignItems="center" gap={1}>
                      <Iconify icon={METRIC_TYPE_ICONS.custom_attribute} width={16} sx={{ color: "text.secondary" }} />
                      <Typography
                        variant="body2" sx={{ flex: 1, cursor: "pointer", "&:hover": { color: "primary.main" } }}
                        onClick={(e) => openPicker(e, "breakdown", i)}
                      >
                        {b.name}
                      </Typography>
                      <IconButton
                        className="breakdown-hover-action" size="small" aria-label={`Remove ${b.name}`}
                        onClick={() => {
                          setQ({ breakdowns: [] });
                          setVisibleSeries(null);
                          if (chartType === "pie") setChartType("column");
                        }}
                        sx={{ opacity: 0, transition: "opacity 0.15s" }}
                      >
                        <Iconify icon="mdi:close" width={14} />
                      </IconButton>
                    </Stack>
                  </Box>
                ))}
              </Box>
            </Box>
          )}
          {rightTab === 1 && (
            <Box sx={{ p: 2, overflow: "auto" }}>
              <ChartAxisSettings
                chartKind={kind}
                series={previewSeries}
                colorFor={colorFor}
                axisConfig={axisConfig}
                onUpdateAxis={updateAxis}
                onSetSeriesAxis={setSeriesAxis}
                onResetAxis={(axis) => setAxisConfig((prev) => ({ ...prev, [axis]: axis === "leftY" ? defaultLeftYAxis() : defaultRightYAxis() }))}
                theme={theme}
              />
            </Box>
          )}
        </Box>
      </Box>

      <SimQueryPicker
        open={picker.open}
        anchorEl={picker.anchor}
        mode={picker.mode}
        categories={picker.mode === "metric" ? SIM_METRIC_CATEGORIES : SIM_ATTRIBUTE_CATEGORIES}
        items={pickerItems}
        onSelect={handlePickerSelect}
        onClose={closePicker}
      />
      <SimFilterValuePicker
        anchorEl={valuePicker.anchor}
        values={activeValueFilter ? distinctAttributeValues(activeValueFilter.id, runs) : []}
        selected={activeValueFilter?.value || []}
        onClose={() => setValuePicker({ anchor: null, scope: null, index: null, metricIndex: null })}
        onApply={(value) => {
          updateFilter(valuePicker.scope, valuePicker.index, { value }, valuePicker.metricIndex);
          setValuePicker({ anchor: null, scope: null, index: null, metricIndex: null });
        }}
      />
    </Box>
  );
}

/* ── filter row (global + per-metric) ── */
function FilterRow({ filter, compact, onPickAttr, onRemove, onOperator, onPickValues, theme }) {
  return (
    <Box
      sx={compact
        ? { mt: 1, pl: 1, borderLeft: `2px solid ${theme.palette.primary.main}` }
        : { mt: 1, p: 1.5, border: `1px solid ${theme.palette.divider}`, borderRadius: 1, "&:hover .filter-hover-action": { opacity: 1 } }}
    >
      <Stack direction="row" alignItems="center" gap={compact ? 0.5 : 1}>
        <Iconify icon={compact ? "mdi:filter-outline" : METRIC_TYPE_ICONS.custom_attribute} width={compact ? 14 : 16} sx={{ color: compact ? "primary.main" : "text.secondary" }} />
        <Typography
          variant={compact ? "caption" : "body2"}
          sx={{ flex: 1, fontWeight: compact ? 500 : undefined, cursor: "pointer", "&:hover": { color: "primary.main" } }}
          onClick={onPickAttr}
        >
          {filter.name || "Select attribute"}
        </Typography>
        <IconButton
          className="filter-hover-action" size="small" onClick={onRemove} aria-label={`Remove filter ${filter.name}`}
          sx={compact ? { p: 0.25 } : { opacity: 0, transition: "opacity 0.15s" }}
        >
          <Iconify icon="mdi:close" width={compact ? 12 : 14} />
        </IconButton>
      </Stack>
      <Stack direction="row" alignItems="center" gap={1} sx={{ mt: compact ? 0.5 : 1 }}>
        <FormControl size="small" sx={{ minWidth: 70 }}>
          <Select value={filter.operator} onChange={(e) => onOperator(e.target.value)} variant="standard" sx={{ fontSize: compact ? "12px" : "13px" }}>
            {SIM_FILTER_OPERATORS.map((op) => <MenuItem key={op.value} value={op.value}>{op.label}</MenuItem>)}
          </Select>
        </FormControl>
        <Typography
          variant={compact ? "caption" : "body2"}
          onClick={onPickValues}
          sx={{
            flex: 1, cursor: "pointer", color: filter.value?.length ? "text.primary" : "text.disabled",
            borderBottom: `1px solid ${theme.palette.divider}`, pb: 0.25,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            "&:hover": { borderColor: "primary.main" },
          }}
        >
          {valueLabel(filter.value)}
        </Typography>
      </Stack>
    </Box>
  );
}

FilterRow.propTypes = {
  filter: PropTypes.shape({
    name: PropTypes.string,
    operator: PropTypes.string,
    value: PropTypes.array,
  }).isRequired,
  compact: PropTypes.bool,
  onPickAttr: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onOperator: PropTypes.func.isRequired,
  onPickValues: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
};

/* ── series table — same markup as the dashboards editor's ── */
function SeriesTable({ series, buckets, visible, setVisible, search, setSearch, colorFor, aggColumnLabel, leftY, theme }) {
  const allIdx = series.map((_, i) => i);
  const isOn = (s) => !visible || visible.has(s.key);
  const allChecked = !visible || visible.size === series.length;
  const someChecked = !!visible && visible.size > 0 && visible.size < series.length;
  const toggle = (s) => {
    const cur = visible || new Set(series.map((x) => x.key));
    const next = new Set(cur);
    if (next.has(s.key)) next.delete(s.key); else next.add(s.key);
    setVisible(next.size === series.length ? null : next);
  };
  const rows = allIdx
    .filter((i) => !search.trim() || series[i].name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (series[b].total ?? -Infinity) - (series[a].total ?? -Infinity));
  const head = { padding: "8px 12px", color: theme.palette.text.secondary, fontSize: "12px", position: "sticky", top: 0, background: theme.palette.background.paper, borderBottom: `1px solid ${theme.palette.divider}` };
  const fmt = (v, unit) => formatValueWithConfig(v, unit && !leftY.unit ? { ...leftY, ...getUnitRendering(unit) } : leftY);

  return (
    <Box sx={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Box sx={{ px: 2, py: 1, display: "flex", alignItems: "center", gap: 1.5, borderBottom: `1px solid ${theme.palette.divider}` }}>
        <TextField
          size="small" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)}
          InputProps={{ startAdornment: <InputAdornment position="start"><Iconify icon="eva:search-fill" width={16} sx={{ color: "text.disabled" }} /></InputAdornment> }}
          sx={{ flex: 1, maxWidth: 300, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
        />
      </Box>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", tableLayout: "auto" }}>
          <thead>
            <tr>
              <th style={{ ...head, textAlign: "left", fontWeight: 500, left: 0, zIndex: 3, minWidth: 220 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <Checkbox
                    size="small" checked={allChecked} indeterminate={someChecked}
                    onChange={() => setVisible(allChecked ? new Set() : null)}
                    sx={{ p: 0, mr: 0.25 }}
                  />
                  <span style={{ fontWeight: 600, color: theme.palette.text.primary }}>Metric</span>
                  <span style={{ color: theme.palette.text.disabled }}>{series.length}</span>
                </span>
              </th>
              <th style={{ ...head, textAlign: "right", fontWeight: 500, minWidth: 90, borderLeft: `1px solid ${theme.palette.divider}`, zIndex: 2 }}>
                {aggColumnLabel}
              </th>
              {buckets.map((b) => (
                <th key={b.key} style={{ ...head, textAlign: "right", fontWeight: 400, whiteSpace: "nowrap", minWidth: 80, zIndex: 2 }}>
                  {b.label}
                  <div style={{ color: theme.palette.text.disabled, fontSize: 11 }}>{b.sub}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((si) => {
              const s = series[si];
              const color = colorFor(s.name);
              /* Aggregated once over every row in range — the same number a
                 single-run view would give if the range were one run. */
              const agg = s.total;
              return (
                <tr key={s.key} style={{ borderBottom: `1px solid ${theme.palette.divider}` }}>
                  <td style={{ padding: "8px 12px", position: "sticky", left: 0, background: theme.palette.background.paper, zIndex: 1, cursor: "pointer" }} onClick={() => toggle(s)}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <Checkbox size="small" checked={isOn(s)} tabIndex={-1} sx={{ p: 0, color, "&.Mui-checked": { color } }} />
                      <span style={{ color: theme.palette.text.primary, fontWeight: 500, fontSize: "13px" }}>{s.name}</span>
                    </span>
                  </td>
                  <td style={{ textAlign: "right", padding: "8px 12px", color: theme.palette.text.primary, fontWeight: 500, fontVariantNumeric: "tabular-nums", borderLeft: `1px solid ${theme.palette.divider}` }}>
                    {agg == null ? "—" : fmt(agg, s.unit)}
                  </td>
                  {s.data.map((pt, ci) => (
                    <td key={ci} style={{ textAlign: "right", padding: "8px 12px", color: theme.palette.text.primary, fontVariantNumeric: "tabular-nums" }}>
                      {pt.y != null
                        ? pt.y >= 1000
                          ? pt.y.toLocaleString(undefined, { maximumFractionDigits: 0 })
                          : pt.y % 1 === 0 ? pt.y : pt.y.toFixed(2)
                        : "-"}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Box>
    </Box>
  );
}

SeriesTable.propTypes = {
  series: PropTypes.array.isRequired,
  buckets: PropTypes.array.isRequired,
  visible: PropTypes.instanceOf(Set),
  setVisible: PropTypes.func.isRequired,
  search: PropTypes.string.isRequired,
  setSearch: PropTypes.func.isRequired,
  colorFor: PropTypes.func.isRequired,
  aggColumnLabel: PropTypes.string,
  leftY: PropTypes.object.isRequired,
  theme: PropTypes.object.isRequired,
};
