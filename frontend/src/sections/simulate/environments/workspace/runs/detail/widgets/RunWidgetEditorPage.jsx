import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Box,
  Breadcrumbs,
  Button,
  ButtonBase,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  IconButton,
  InputAdornment,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { useEnvironment } from "src/api/simulate-environments/environment";
import { useRunDetail } from "src/api/simulate-environments/runDetail";
import WidgetDescriptionPopover from "src/sections/dashboards/WidgetDescriptionPopover";
import TruncatedTooltipText from "src/sections/dashboards/TruncatedTooltipText";
import { ALL_AGGREGATIONS } from "src/sections/dashboards/constants";
import { getAggColumnLabel } from "src/sections/dashboards/widgetUtils";
import { AggregationPicker } from "src/sections/dashboards/WidgetEditorView";
import {
  CHART_TYPES,
  DATASET_EXTRA_AGGREGATIONS,
  LETTER_LABELS,
  METRIC_TYPE_ICONS,
} from "src/sections/dashboards/widgetEditorConstants";
import SoonChip from "../../../../components/SoonChip";
import UiOnlyFlag from "../../../../components/UiOnlyFlag";
import { getPanelMeta, panelTitle } from "../analytics/panelRegistry";
import {
  DEFAULT_VIEW_NAME,
  newId,
  readLayoutWidget,
  removeLayoutWidget,
  saveLayoutWidget,
} from "../analytics/useRunLayout";
import { CALL_CAP } from "../../../compare/useComparison";
import ChartAxisSettings, {
  defaultAxisConfig,
  defaultLeftYAxis,
  defaultRightYAxis,
} from "./ChartAxisSettings";
import RunQueryPicker, { RunFilterValuePicker } from "./RunQueryPicker";
import RunWidgetChart, { chartKindOf, useSeriesColors } from "./RunWidgetChart";
import { formatRunWidgetValue } from "./formatRunWidgetValue";
import {
  MAX_DEFAULT_SERIES,
  RUN_ATTRIBUTES,
  RUN_ATTRIBUTE_CATEGORIES,
  RUN_FILTER_OPERATORS,
  RUN_METRIC_CATEGORIES,
  RUN_RANGE_PRESETS,
  X_AXIS_OPTIONS,
  attributeAxisValue,
  builtinQuery,
  distinctAttributeValues,
  metricFromCatalog,
  newRunQuery,
  runMetricCatalog,
  runWidgetQuery,
  topSeriesKeys,
} from "./runWidgetQuery";
import { useRunWidgetCalls } from "./useRunWidgetCalls";

/**
 * The run analytics widget editor: the Observe dashboards widget editor on
 * this run's calls. Same header (breadcrumb, inline name, description, more
 * menu, Close, Save), controls row, chart over a series table, and the Query
 * and Chart panel. The query runs in the browser over the run's real calls;
 * Save puts the widget in the Custom widgets section of the analytics view it
 * was opened from. Like the layout, it lasts for this visit only.
 */

const MAX_METRICS = 5;
const NO_VALUES = [];

const VIEW_MODES = [
  { mode: "table", icon: "solar:table-linear", height: 0, tip: "Table" },
  {
    mode: "split-table",
    icon: "solar:panel-top-linear",
    height: 180,
    tip: "Chart + Table",
  },
  {
    mode: "split-chart",
    icon: "mdi:page-layout-body",
    height: 350,
    tip: "Chart (expanded)",
  },
  {
    mode: "chart",
    icon: "solar:graph-up-linear",
    height: 600,
    tip: "Chart only",
  },
];

const xAxisLabel = (value) => {
  const option = X_AXIS_OPTIONS.find((item) => item.value === value);
  if (option) return option.label;
  const attribute = RUN_ATTRIBUTES.find(
    (item) => attributeAxisValue(item.id) === value,
  );
  return attribute ? `By ${attribute.name.toLowerCase()}` : value;
};

const valueLabel = (value) => {
  if (!Array.isArray(value) || !value.length) return "Select values";
  if (value.length <= 2) return value.join(", ");
  return `${value.slice(0, 2).join(", ")} +${value.length - 2}`;
};

const mergeAxisConfig = (saved) => {
  const base = defaultAxisConfig();
  if (!saved) return base;
  return {
    ...base,
    ...saved,
    leftY: { ...base.leftY, ...(saved.leftY || {}) },
    rightY: { ...base.rightY, ...(saved.rightY || {}) },
    xAxis: { ...base.xAxis, ...(saved.xAxis || {}) },
    seriesAxis: saved.seriesAxis || {},
  };
};

function SectionHeader({ title, required, onClick, disabled, tip }) {
  return (
    <CustomTooltip show title={tip} placement="left" arrow size="small">
      <ButtonBase
        aria-label={`Add ${title.toLowerCase()}`}
        disabled={disabled}
        onClick={onClick}
        sx={{
          width: "calc(100% + 16px)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderRadius: 1,
          px: 1,
          py: 0.5,
          mx: -1,
          transition: "background-color 0.15s",
          "&:hover": {
            bgcolor: "action.hover",
            "& .section-title": { color: "primary.main" },
          },
        }}
      >
        <Typography
          className="section-title"
          typography="s1"
          sx={{ fontWeight: 600, transition: "color 0.15s" }}
        >
          {title}
          {required && (
            <Typography component="span" sx={{ color: "error.main" }}>
              *
            </Typography>
          )}
        </Typography>
        <Iconify
          icon="solar:add-linear"
          width={20}
          sx={{ color: disabled ? "text.disabled" : "text.secondary" }}
        />
      </ButtonBase>
    </CustomTooltip>
  );
}
SectionHeader.propTypes = {
  title: PropTypes.string.isRequired,
  required: PropTypes.bool,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  tip: PropTypes.string,
};

// Only this run is readable; the earlier-run ranges stay in view, disabled.
function RangePills({ value, onSelect }) {
  return (
    <Stack direction="row" alignItems="center" gap={1} sx={{ flexShrink: 0 }}>
      <Box
        role="group"
        aria-label="Runs in range"
        sx={{
          display: "inline-flex",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        {RUN_RANGE_PRESETS.map((preset, index) => {
          const active = value === preset.value;
          return (
            <ButtonBase
              key={preset.value}
              disabled={preset.soon}
              aria-pressed={active}
              onClick={() => onSelect(preset.value)}
              sx={{
                px: 1.5,
                py: 0.6,
                typography: "s1",
                fontWeight: active ? 600 : 400,
                color: active ? "text.primary" : "text.secondary",
                bgcolor: active ? "action.selected" : "transparent",
                borderRight:
                  index < RUN_RANGE_PRESETS.length - 1 ? "1px solid" : "none",
                borderColor: "divider",
                whiteSpace: "nowrap",
                "&.Mui-disabled": { opacity: 0.4 },
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              {preset.label}
            </ButtonBase>
          );
        })}
      </Box>
      <SoonChip label="Run history" what="Calls from earlier runs" plural />
    </Stack>
  );
}
RangePills.propTypes = {
  value: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
};

function ViewModeBar({ viewMode, onSelect, onDragStart, isDragging }) {
  return (
    <Box
      onMouseDown={onDragStart}
      sx={{
        position: "relative",
        flexShrink: 0,
        cursor: "row-resize",
        py: 0.5,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        "&:hover .divider-line": { bgcolor: "primary.main" },
      }}
    >
      <Box
        className="divider-line"
        sx={{
          position: "absolute",
          left: 0,
          right: 0,
          top: "50%",
          transform: "translateY(-50%)",
          height: 3,
          bgcolor: isDragging ? "primary.main" : "divider",
          transition: "background-color 0.15s",
        }}
      />
      <Stack
        direction="row"
        sx={{
          position: "relative",
          zIndex: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          bgcolor: "background.paper",
        }}
      >
        {VIEW_MODES.map(({ mode, icon, height, tip }) => (
          <CustomTooltip
            key={mode}
            show
            title={tip}
            placement="top"
            size="small"
          >
            <IconButton
              size="small"
              aria-label={tip}
              aria-pressed={viewMode === mode}
              onClick={(event) => {
                event.stopPropagation();
                onSelect(mode, height);
              }}
              onMouseDown={(event) => event.stopPropagation()}
              sx={{
                borderRadius: 0,
                bgcolor: viewMode === mode ? "action.selected" : "transparent",
              }}
            >
              <Iconify
                icon={icon}
                width={20}
                sx={{
                  color: viewMode === mode ? "text.primary" : "text.disabled",
                }}
              />
            </IconButton>
          </CustomTooltip>
        ))}
      </Stack>
    </Box>
  );
}
ViewModeBar.propTypes = {
  viewMode: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onDragStart: PropTypes.func,
  isDragging: PropTypes.bool,
};

function FilterRow({
  filter,
  compact,
  onPickAttr,
  onRemove,
  onOperator,
  onPickValues,
}) {
  return (
    <Box
      sx={
        compact
          ? {
              mt: 1,
              pl: 1,
              borderLeft: "2px solid",
              borderColor: "primary.main",
            }
          : {
              mt: 1,
              p: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }
      }
    >
      <Stack direction="row" alignItems="center" gap={compact ? 0.5 : 1}>
        <Iconify
          icon={
            compact ? "solar:filter-linear" : METRIC_TYPE_ICONS.custom_attribute
          }
          width={compact ? 14 : 16}
          sx={{ color: compact ? "primary.main" : "text.secondary" }}
        />
        <ButtonBase
          onClick={onPickAttr}
          sx={{
            flex: 1,
            justifyContent: "flex-start",
            typography: compact ? "s2" : "s1",
            fontWeight: compact ? 500 : 400,
            "&:hover": { color: "primary.main" },
          }}
        >
          {filter.name || "Select attribute"}
        </ButtonBase>
        <IconButton
          size="small"
          onClick={onRemove}
          aria-label={`Remove filter ${filter.name}`}
          sx={{ p: 0.25 }}
        >
          <Iconify icon="solar:close-linear" width={compact ? 12 : 14} />
        </IconButton>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ mt: compact ? 0.5 : 1 }}
      >
        <FormControl size="small" sx={{ minWidth: 70 }}>
          <Select
            value={filter.operator}
            onChange={(event) => onOperator(event.target.value)}
            variant="standard"
            inputProps={{ "aria-label": `${filter.name} operator` }}
            sx={{ typography: compact ? "s2" : "s1" }}
          >
            {RUN_FILTER_OPERATORS.map((operator) => (
              <MenuItem key={operator.value} value={operator.value}>
                {operator.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <ButtonBase
          onClick={onPickValues}
          sx={{
            flex: 1,
            minWidth: 0,
            justifyContent: "flex-start",
            typography: compact ? "s2" : "s1",
            color: filter.value?.length ? "text.primary" : "text.disabled",
            borderBottom: "1px solid",
            borderColor: "divider",
            pb: 0.25,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            "&:hover": { borderColor: "primary.main" },
          }}
        >
          {valueLabel(filter.value)}
        </ButtonBase>
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
};

// The dashboards editor's series table: a row per series with its value over
// the run and one column per x-axis bucket; ticking a row shows it on the
// chart.
function SeriesTable({
  series,
  buckets,
  visible,
  setVisible,
  search,
  setSearch,
  colorFor,
  aggColumnLabel,
  leftY,
}) {
  const theme = useTheme();
  const isOn = (item) => !visible || visible.has(item.key);
  const allChecked = !visible || visible.size === series.length;
  const someChecked =
    !!visible && visible.size > 0 && visible.size < series.length;
  const toggle = (item) => {
    const current = visible || new Set(series.map((entry) => entry.key));
    const next = new Set(current);
    if (next.has(item.key)) next.delete(item.key);
    else next.add(item.key);
    setVisible(next.size === series.length ? null : next);
  };
  const rows = series
    .filter(
      (item) =>
        !search.trim() ||
        item.name.toLowerCase().includes(search.trim().toLowerCase()),
    )
    .sort((a, b) => (b.total ?? -Infinity) - (a.total ?? -Infinity));
  const head = {
    padding: "8px 12px",
    color: theme.palette.text.secondary,
    fontSize: 12,
    position: "sticky",
    top: 0,
    background: theme.palette.background.paper,
    borderBottom: `1px solid ${theme.palette.divider}`,
  };
  const fmt = (value, unit) => formatRunWidgetValue(value, leftY, unit);
  const cell = {
    textAlign: "right",
    padding: "8px 12px",
    color: theme.palette.text.primary,
    fontVariantNumeric: "tabular-nums",
  };

  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <Box
        sx={{
          px: 2,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <TextField
          size="small"
          placeholder="Search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{
            maxWidth: 300,
            "& .MuiOutlinedInput-root": { typography: "s1" },
          }}
        />
      </Box>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <table
          aria-label="Series"
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
        >
          <thead>
            <tr>
              <th
                style={{
                  ...head,
                  textAlign: "left",
                  fontWeight: 500,
                  left: 0,
                  zIndex: 3,
                  minWidth: 220,
                }}
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={allChecked}
                    indeterminate={someChecked}
                    onChange={() => setVisible(allChecked ? new Set() : null)}
                    inputProps={{ "aria-label": "Show every series" }}
                    sx={{ p: 0, mr: 0.25 }}
                  />
                  <span
                    style={{
                      fontWeight: 600,
                      color: theme.palette.text.primary,
                    }}
                  >
                    Metric
                  </span>
                  <span style={{ color: theme.palette.text.disabled }}>
                    {series.length}
                  </span>
                </span>
              </th>
              <th
                style={{
                  ...head,
                  textAlign: "right",
                  fontWeight: 500,
                  minWidth: 90,
                  borderLeft: `1px solid ${theme.palette.divider}`,
                  zIndex: 2,
                }}
              >
                {aggColumnLabel}
              </th>
              {buckets.map((bucket) => (
                <th
                  key={bucket.key}
                  style={{
                    ...head,
                    textAlign: "right",
                    fontWeight: 400,
                    whiteSpace: "nowrap",
                    minWidth: 80,
                    zIndex: 2,
                  }}
                >
                  {bucket.label}
                  <div
                    style={{ color: theme.palette.text.disabled, fontSize: 12 }}
                  >
                    {bucket.sub}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => {
              const color = colorFor(item.name);
              return (
                <tr
                  key={item.key}
                  style={{ borderBottom: `1px solid ${theme.palette.divider}` }}
                >
                  <td
                    style={{
                      padding: "8px 12px",
                      position: "sticky",
                      left: 0,
                      background: theme.palette.background.paper,
                      zIndex: 1,
                    }}
                  >
                    <Box
                      component="label"
                      sx={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 0.75,
                        cursor: "pointer",
                      }}
                    >
                      <Checkbox
                        size="small"
                        checked={isOn(item)}
                        onChange={() => toggle(item)}
                        sx={{ p: 0, color, "&.Mui-checked": { color } }}
                      />
                      <Typography typography="s1" sx={{ fontWeight: 500 }}>
                        {item.name}
                      </Typography>
                    </Box>
                  </td>
                  <td
                    style={{
                      ...cell,
                      fontWeight: 500,
                      borderLeft: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    {item.total == null ? "-" : fmt(item.total, item.unit)}
                  </td>
                  {item.data.map((point, index) => (
                    <td key={buckets[index]?.key ?? index} style={cell}>
                      {point.y == null
                        ? "-"
                        : item.unit === "ms"
                          ? fmt(point.y, item.unit)
                          : point.y >= 1000
                            ? point.y.toLocaleString(undefined, {
                                maximumFractionDigits: 0,
                              })
                            : point.y % 1 === 0
                              ? point.y
                              : point.y.toFixed(2)}
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
};

const metricCardSx = {
  mt: 1,
  p: 1.5,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
};

export default function RunWidgetEditorPage() {
  const { envId, testId, executionId, widgetId } = useParams();
  const [searchParams] = useSearchParams();
  const viewParam = searchParams.get("view");
  const navigate = useNavigate();
  const theme = useTheme();
  const { env, isLoading: envLoading } = useEnvironment(envId);
  const runDetail = useRunDetail(undefined, executionId);
  const ordinal = runDetail?.identity?.ordinal;
  const noun = runDetail?.stats?.agentType === "text" ? "chat" : "call";
  const calls = useRunWidgetCalls(executionId);
  const tasks = calls.tasks;
  const catalog = useMemo(() => runMetricCatalog(tasks), [tasks]);
  const runLabel = ordinal != null ? `Run ${ordinal}` : "This run";

  const isNew = widgetId === "new";
  // What this page edits: a saved query widget, a copy of a built-in, a
  // built-in, or a blank new widget. A saved one is updated in place; a
  // built-in is never replaced, its edit is added as a new widget.
  const stored = useMemo(
    () => (isNew ? null : readLayoutWidget(envId, widgetId, viewParam)),
    [isNew, envId, widgetId, viewParam],
  );
  const queryWidget = stored?.kind === "query" ? stored : null;
  const builtinId = queryWidget
    ? null
    : stored?.of || (getPanelMeta(widgetId) ? widgetId : null);

  const analyticsUrl = (view) => {
    const params = new URLSearchParams({ tab: "analytics" });
    if (view && view !== DEFAULT_VIEW_NAME) params.set("view", view);
    return `${paths.dashboard.simulate.environments.execution(
      envId,
      testId,
      executionId,
    )}?${params.toString()}`;
  };
  const backUrl = analyticsUrl(viewParam);

  const [initialized, setInitialized] = useState(false);
  const [builtinNote, setBuiltinNote] = useState(null);
  const [chartName, setChartName] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [chartDescription, setChartDescription] = useState("");
  const [descOpen, setDescOpen] = useState(false);
  const descSlotRef = useRef(null);
  const [chartType, setChartType] = useState("line");
  const [query, setQuery] = useState(newRunQuery);
  const [axisConfig, setAxisConfig] = useState(defaultAxisConfig);
  const [visibleSeries, setVisibleSeries] = useState(null);
  const [viewMode, setViewMode] = useState("split-chart");
  const [chartHeight, setChartHeight] = useState(350);
  const [isDragging, setIsDragging] = useState(false);
  const [rightTab, setRightTab] = useState(0);
  const [tableSearch, setTableSearch] = useState("");
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [picker, setPicker] = useState({
    open: false,
    anchor: null,
    mode: "metric",
    index: null,
    metricIndex: null,
  });
  const closedValuePicker = {
    anchor: null,
    scope: null,
    index: null,
    metricIndex: null,
  };
  const [valuePicker, setValuePicker] = useState(closedValuePicker);

  // Load the widget once the calls are in: the metric catalogue (evals in
  // particular) is built from them.
  useEffect(() => {
    if (initialized || calls.isLoading) return;
    let saved = null;
    if (queryWidget) saved = queryWidget;
    else if (builtinId) {
      const built = builtinQuery(builtinId, catalog);
      saved = {
        title: stored?.title || panelTitle(builtinId, noun),
        ...(built || {}),
      };
      setBuiltinNote(built ? "query" : "blank");
    }
    if (saved) {
      setChartName(saved.title || "");
      setChartDescription(saved.description || "");
      setChartType(saved.chart_type || "line");
      setQuery({ ...newRunQuery(), ...(saved.query || {}), range: "this" });
      setAxisConfig(mergeAxisConfig(saved.axis_config));
      if (Array.isArray(saved.visible_series))
        setVisibleSeries(new Set(saved.visible_series));
    }
    setInitialized(true);
  }, [
    initialized,
    calls.isLoading,
    queryWidget,
    builtinId,
    stored,
    catalog,
    noun,
  ]);

  const result = useMemo(
    () => runWidgetQuery(query, tasks, { catalog, runLabel }),
    [query, tasks, catalog, runLabel],
  );
  const previewSeries = result.series;
  const buckets = result.buckets;
  const colorFor = useSeriesColors(previewSeries);

  // Past ten series, start with the top ten ticked so the chart stays
  // readable; the rest are one click away in the table.
  const seriesSig = previewSeries.map((item) => item.key).join("\u0001");
  const lastSigRef = useRef(null);
  useEffect(() => {
    if (!initialized || lastSigRef.current === seriesSig) return;
    const firstLoad = lastSigRef.current === null;
    lastSigRef.current = seriesSig;
    if (firstLoad && visibleSeries) return;
    if (chartType === "table" || chartType === "metric") {
      setVisibleSeries(null);
      return;
    }
    setVisibleSeries(
      previewSeries.length > MAX_DEFAULT_SERIES
        ? new Set(topSeriesKeys(previewSeries))
        : null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesSig, initialized]);

  const kind = chartKindOf(chartType);
  const isPie = chartType === "pie";
  const isTable = chartType === "table";
  const isMetricCard = chartType === "metric";
  const isHorizontal = chartType === "bar" || chartType === "stacked_bar";
  const hasBreakdown = query.breakdowns.length > 0;
  const showSplit =
    previewSeries.length > 0 &&
    !isMetricCard &&
    !isHorizontal &&
    !isTable &&
    !isPie;

  const setQ = (patch) => setQuery((current) => ({ ...current, ...patch }));
  const updateAxis = (axis, key, value) =>
    setAxisConfig((previous) => ({
      ...previous,
      [axis]: { ...previous[axis], [key]: value },
    }));
  const setSeriesAxis = (index, side) =>
    setAxisConfig((previous) => {
      const seriesAxis = { ...previous.seriesAxis, [index]: side };
      return {
        ...previous,
        seriesAxis,
        rightY: {
          ...previous.rightY,
          visible: Object.values(seriesAxis).includes("right"),
        },
      };
    });

  const dragStart = useRef(null);
  const handleDragStart = useCallback(
    (event) => {
      event.preventDefault();
      dragStart.current = { y: event.clientY, h: chartHeight };
      setIsDragging(true);
      const move = (moveEvent) => {
        const next = Math.max(
          0,
          Math.min(
            700,
            dragStart.current.h + (moveEvent.clientY - dragStart.current.y),
          ),
        );
        setChartHeight(next);
        setViewMode(
          next === 0
            ? "table"
            : next >= 600
              ? "chart"
              : next >= 300
                ? "split-chart"
                : "split-table",
        );
      };
      const up = () => {
        setIsDragging(false);
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    },
    [chartHeight],
  );

  const openPicker = (event, mode, index = null, metricIndex = null) =>
    setPicker({
      open: true,
      anchor: event.currentTarget,
      mode,
      index,
      metricIndex,
    });
  const closePicker = () =>
    setPicker((current) => ({ ...current, open: false }));
  const pickerItems = useMemo(() => {
    if (picker.mode === "metric") return catalog;
    return RUN_ATTRIBUTES.map((attribute) => ({
      ...attribute,
      type: "custom_attribute",
    }));
  }, [picker.mode, catalog]);

  const handlePickerSelect = (option) => {
    const { mode, index, metricIndex } = picker;
    const filter = {
      id: option.id,
      name: option.name,
      operator: "is",
      value: [],
    };
    if (mode === "metric") {
      const next = metricFromCatalog(
        catalog.find((item) => item.id === option.id),
      );
      const metrics = [...query.metrics];
      if (index != null)
        metrics[index] = { ...next, filters: metrics[index]?.filters || [] };
      else metrics.push(next);
      setQ({ metrics });
    } else if (mode === "breakdown") {
      setQ({ breakdowns: [{ id: option.id, name: option.name }] });
      setVisibleSeries(null);
    } else if (mode === "filter") {
      const filters = [...query.filters];
      if (index != null) filters[index] = filter;
      else filters.push(filter);
      setQ({ filters });
    } else if (mode === "metric_filter") {
      const metrics = [...query.metrics];
      const metricFilters = [...(metrics[metricIndex].filters || [])];
      if (index != null) metricFilters[index] = filter;
      else metricFilters.push(filter);
      metrics[metricIndex] = {
        ...metrics[metricIndex],
        filters: metricFilters,
      };
      setQ({ metrics });
    }
    closePicker();
  };

  const updateFilter = (scope, index, patch, metricIndex) => {
    if (scope === "metric") {
      const metrics = [...query.metrics];
      const metric = { ...metrics[metricIndex] };
      metric.filters = metric.filters.map((filter, i) =>
        i === index ? { ...filter, ...patch } : filter,
      );
      metrics[metricIndex] = metric;
      setQ({ metrics });
    } else {
      setQ({
        filters: query.filters.map((filter, i) =>
          i === index ? { ...filter, ...patch } : filter,
        ),
      });
    }
  };
  const activeValueFilter =
    valuePicker.scope === "metric"
      ? query.metrics[valuePicker.metricIndex]?.filters?.[valuePicker.index]
      : valuePicker.scope === "global"
        ? query.filters[valuePicker.index]
        : null;

  const snapshot = () => ({
    kind: "query",
    title: chartName.trim() || "Untitled widget",
    description: chartDescription.trim(),
    chart_type: chartType,
    query: { ...query, range: "this" },
    axis_config: axisConfig,
    visible_series: visibleSeries ? [...visibleSeries] : null,
  });

  const handleSave = () => {
    if (!query.metrics.length) return;
    // A saved query or a copy is replaced in place; anything else is new.
    const id = stored ? stored.id : newId("query");
    const view = saveLayoutWidget(envId, { id, ...snapshot() }, viewParam);
    navigate(analyticsUrl(view));
  };

  const handleDelete = () => {
    if (stored) removeLayoutWidget(envId, stored.id, viewParam);
    navigate(backUrl);
  };

  const handleDuplicate = () => {
    saveLayoutWidget(
      envId,
      {
        id: newId("query"),
        ...snapshot(),
        title: `${chartName.trim() || "Untitled widget"} (copy)`,
      },
      viewParam,
    );
    enqueueSnackbar("Copy added to Custom widgets", { variant: "success" });
  };

  const aggColumnLabel = getAggColumnLabel(query.metrics, [
    ...ALL_AGGREGATIONS,
    ...DATASET_EXTRA_AGGREGATIONS,
  ]);
  const toCsv = () =>
    [
      ["Metric", aggColumnLabel, ...buckets.map((bucket) => bucket.label)],
      ...previewSeries.map((item) => [
        item.name,
        item.total == null ? "" : item.total.toFixed(2),
        ...item.data.map((point) => (point.y != null ? point.y : "")),
      ]),
    ]
      .map((row) =>
        row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","),
      )
      .join("\n");
  const exportCsv = () => {
    const url = URL.createObjectURL(new Blob([toCsv()], { type: "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${chartName.trim() || "widget"}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (envLoading) {
    return (
      <Stack alignItems="center" sx={{ py: 8 }}>
        <CircularProgress size={26} />
      </Stack>
    );
  }

  const trimmedDescription = chartDescription.trim();
  const chartProps = {
    series: previewSeries,
    buckets,
    chartType,
    axisConfig,
    visible: visibleSeries,
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          gap: 1,
          minHeight: 48,
        }}
      >
        <Breadcrumbs
          separator={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={{ flexShrink: 0 }}
        >
          <Link
            component="button"
            underline="hover"
            color="text.secondary"
            typography="s1"
            onClick={() =>
              navigate(
                paths.dashboard.simulate.environments.workspaceTab(
                  envId,
                  "runs",
                ),
              )
            }
          >
            {env?.name || "Environment"}
          </Link>
          <Link
            component="button"
            underline="hover"
            color="text.secondary"
            typography="s1"
            sx={{ whiteSpace: "nowrap" }}
            onClick={() => navigate(backUrl)}
          >
            {runLabel} · Analytics
          </Link>
        </Breadcrumbs>
        <Iconify
          icon="solar:alt-arrow-right-linear"
          width={14}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
        {editingName ? (
          <TextField
            value={chartName}
            onChange={(event) => setChartName(event.target.value)}
            onBlur={() => setEditingName(false)}
            onKeyDown={(event) => {
              if (event.key === "Enter") setEditingName(false);
            }}
            autoFocus
            size="small"
            placeholder="Untitled widget"
            inputProps={{ "aria-label": "Widget name" }}
            sx={{
              minWidth: 200,
              maxWidth: 350,
              "& .MuiOutlinedInput-input": {
                py: 0.5,
                typography: "s1",
                fontWeight: 500,
              },
            }}
          />
        ) : (
          <ButtonBase
            onClick={() => setEditingName(true)}
            sx={{
              typography: "s1",
              fontWeight: 500,
              maxWidth: 300,
              display: "block",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              "&:hover": { color: "primary.main" },
            }}
          >
            {chartName || "Untitled widget"}
          </ButtonBase>
        )}
        <Box ref={descSlotRef} sx={{ display: "flex", minWidth: 0 }}>
          {trimmedDescription ? (
            <TruncatedTooltipText text={trimmedDescription}>
              {(measureRef) => (
                <ButtonBase
                  aria-label="Edit description"
                  onClick={() => setDescOpen(true)}
                  sx={{
                    minWidth: 0,
                    maxWidth: 260,
                    px: 0.75,
                    py: 0.25,
                    gap: 0.5,
                    borderRadius: 1,
                    color: "text.secondary",
                    "&:hover": {
                      bgcolor: "action.hover",
                      color: "text.primary",
                    },
                  }}
                >
                  <Iconify
                    icon="solar:notes-linear"
                    width={14}
                    sx={{ flexShrink: 0 }}
                  />
                  <Typography
                    ref={measureRef}
                    noWrap
                    typography="s1"
                    sx={{ minWidth: 0 }}
                  >
                    {trimmedDescription}
                  </Typography>
                </ButtonBase>
              )}
            </TruncatedTooltipText>
          ) : (
            <Button
              size="small"
              startIcon={<Iconify icon="solar:add-linear" width={14} />}
              onClick={() => setDescOpen(true)}
              sx={{
                flexShrink: 0,
                color: "text.disabled",
                "&:hover": { color: "text.secondary" },
              }}
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
        <IconButton
          size="small"
          aria-label="More actions"
          onClick={(event) => setMoreAnchor(event.currentTarget)}
          sx={{ color: "text.secondary" }}
        >
          <Iconify icon="solar:menu-dots-linear" width={20} />
        </IconButton>
        <Menu
          anchorEl={moreAnchor}
          open={!!moreAnchor}
          onClose={() => setMoreAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 180 } } }}
        >
          {stored && (
            <MenuItem
              onClick={() => {
                setMoreAnchor(null);
                setConfirmDelete(true);
              }}
              sx={{ color: "error.main" }}
            >
              <ListItemIcon sx={{ color: "inherit" }}>
                <Iconify icon="solar:trash-bin-trash-linear" width={16} />
              </ListItemIcon>
              <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                Delete
              </ListItemText>
            </MenuItem>
          )}
          <MenuItem
            onClick={() => {
              setMoreAnchor(null);
              setEditingName(true);
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:pen-linear" width={16} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Rename
            </ListItemText>
          </MenuItem>
          <MenuItem
            disabled={!query.metrics.length}
            onClick={() => {
              setMoreAnchor(null);
              handleDuplicate();
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:copy-linear" width={16} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Duplicate
            </ListItemText>
          </MenuItem>
          <Divider />
          <MenuItem
            disabled={!previewSeries.length}
            onClick={() => {
              setMoreAnchor(null);
              exportCsv();
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:download-minimalistic-linear" width={16} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Export CSV
            </ListItemText>
          </MenuItem>
          <MenuItem
            disabled={!previewSeries.length}
            onClick={() => {
              setMoreAnchor(null);
              navigator.clipboard?.writeText(toCsv());
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:copy-linear" width={16} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Copy CSV
            </ListItemText>
          </MenuItem>
        </Menu>
        <ConfirmDialog
          open={confirmDelete}
          onClose={() => setConfirmDelete(false)}
          title="Delete Widget"
          content={`Are you sure you want to delete "${
            chartName || "this widget"
          }"? This action cannot be undone.`}
          action={
            <Button
              variant="contained"
              color="error"
              size="small"
              onClick={handleDelete}
            >
              Delete
            </Button>
          }
        />
        <Button
          onClick={() => navigate(backUrl)}
          sx={{ color: "text.primary" }}
        >
          Close
        </Button>
        <UiOnlyFlag feature="analyticsViews" />
        <CustomTooltip
          show={!query.metrics.length}
          title="Add at least one metric"
          size="small"
        >
          <span>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={!query.metrics.length}
            >
              Save
            </Button>
          </span>
        </CustomTooltip>
      </Stack>

      <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <Box
          sx={{
            flex: 1,
            p: 2,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            overflow: "auto",
            minWidth: 0,
          }}
        >
          <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
            <RangePills
              value={query.range}
              onSelect={(range) => setQ({ range })}
            />
            <Box sx={{ flex: 1, minWidth: 0 }} />
            <FormControl size="small" sx={{ minWidth: 80 }}>
              <Select
                value={query.xAxis}
                onChange={(event) => setQ({ xAxis: event.target.value })}
                inputProps={{ "aria-label": "X-axis" }}
                sx={{ typography: "s1", "& .MuiSelect-select": { py: 0.7 } }}
                renderValue={(value) => xAxisLabel(value)}
                MenuProps={{ PaperProps: { sx: { maxHeight: 420 } } }}
              >
                {X_AXIS_OPTIONS.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
                <Divider />
                {RUN_ATTRIBUTES.map((attribute) => (
                  <MenuItem
                    key={attribute.id}
                    value={attributeAxisValue(attribute.id)}
                  >
                    By {attribute.name.toLowerCase()}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 120 }}>
              <Select
                value={chartType}
                onChange={(event) => setChartType(event.target.value)}
                inputProps={{ "aria-label": "Chart type" }}
                sx={{ typography: "s1", "& .MuiSelect-select": { py: 0.7 } }}
                renderValue={(value) => {
                  const type = CHART_TYPES.find((item) => item.value === value);
                  return (
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      <Iconify
                        icon={type?.icon || "solar:graph-up-linear"}
                        width={16}
                      />
                      {type?.label || value}
                    </Stack>
                  );
                }}
              >
                {CHART_TYPES.flatMap((type, index) => {
                  const previous = index > 0 ? CHART_TYPES[index - 1] : null;
                  const pieDisabled = type.value === "pie" && !hasBreakdown;
                  return [
                    previous && previous.group !== type.group && (
                      <Divider key={`divider-${type.value}`} />
                    ),
                    <MenuItem
                      key={type.value}
                      value={type.value}
                      disabled={pieDisabled}
                    >
                      <Stack direction="row" alignItems="center" gap={0.5}>
                        <Iconify icon={type.icon} width={16} />
                        {type.label}
                        {pieDisabled && (
                          <Typography
                            typography="s3"
                            sx={{ color: "text.disabled", ml: 1 }}
                          >
                            add a breakdown
                          </Typography>
                        )}
                      </Stack>
                    </MenuItem>,
                  ].filter(Boolean);
                })}
              </Select>
            </FormControl>
          </Stack>

          <Stack direction="row" alignItems="center" gap={1}>
            {calls.isLoading ? (
              <CircularProgress size={14} />
            ) : (
              <Typography typography="s2" sx={{ color: "text.subtitle" }}>
                {calls.count} {noun}
                {calls.count === 1 ? "" : "s"} in this run
                {calls.capped
                  ? `. Reads the first ${CALL_CAP.toLocaleString()}.`
                  : ""}
              </Typography>
            )}
            {calls.isError && (
              <Chip
                size="small"
                color="error"
                variant="outlined"
                label="Calls could not be loaded"
              />
            )}
          </Stack>

          {builtinNote && (
            <Stack
              direction="row"
              alignItems="flex-start"
              gap={1}
              sx={{
                px: 1.5,
                py: 1,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "action.hover",
              }}
            >
              <Iconify
                icon="solar:info-circle-linear"
                width={16}
                sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
              />
              <Typography typography="s1" sx={{ color: "text.secondary" }}>
                {builtinNote === "query"
                  ? "Closest query to the built-in chart, read from this run's calls."
                  : "This chart reads run totals the call list doesn't carry, so the query starts blank."}{" "}
                Saving adds it to Custom widgets. The built-in stays as it is.
              </Typography>
            </Stack>
          )}

          <Box
            sx={{
              flex: 1,
              minHeight: 420,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            {showSplit ? (
              <>
                {chartHeight > 0 && (
                  <Box
                    sx={{ height: chartHeight, flexShrink: 0, p: 1.5, pb: 0 }}
                  >
                    <RunWidgetChart {...chartProps} />
                  </Box>
                )}
                <ViewModeBar
                  viewMode={viewMode}
                  onSelect={(mode, height) => {
                    setViewMode(mode);
                    setChartHeight(height);
                  }}
                  onDragStart={handleDragStart}
                  isDragging={isDragging}
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
                  />
                )}
              </>
            ) : (
              <Box sx={{ flex: 1, p: 1.5, minHeight: 0 }}>
                <RunWidgetChart {...chartProps} />
              </Box>
            )}
            {isPie && previewSeries.length > 0 && (
              <Box
                sx={{
                  display: "flex",
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                {query.metrics.map((metric, index) => (
                  <Box
                    key={`${metric.id}-${index}`}
                    sx={{
                      flex: 1,
                      textAlign: "center",
                      py: 2,
                      px: 1.5,
                      borderRight:
                        index < query.metrics.length - 1 ? "1px solid" : "none",
                      borderColor: "divider",
                    }}
                  >
                    <Typography typography="s1" sx={{ fontWeight: 600 }}>
                      {LETTER_LABELS[index]} {metric.name}
                    </Typography>
                    <Typography
                      typography="s2"
                      sx={{ color: "text.secondary", display: "block" }}
                    >
                      {metric.aggregation} ·{" "}
                      {
                        previewSeries.filter(
                          (item) => item.metricIndex === index,
                        ).length
                      }{" "}
                      slices
                    </Typography>
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        </Box>

        <Box
          sx={{
            width: 320,
            minWidth: 320,
            borderLeft: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            overflow: "auto",
          }}
        >
          <Box sx={{ px: 2 }}>
            <Tabs value={rightTab} onChange={(_, value) => setRightTab(value)}>
              <Tab label="Query" />
              <Tab label="Chart" />
            </Tabs>
          </Box>
          <Divider />
          {rightTab === 0 && (
            <Box
              sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}
            >
              <Box>
                <SectionHeader
                  title="Metric"
                  required
                  tip="Choose what to measure and track."
                  disabled={query.metrics.length >= MAX_METRICS}
                  onClick={(event) => openPicker(event, "metric")}
                />
                {query.metrics.map((metric, index) => {
                  const def = catalog.find((item) => item.id === metric.id);
                  return (
                    <Box key={`${metric.id}-${index}`} sx={metricCardSx}>
                      <Stack direction="row" alignItems="center" gap={1}>
                        <Chip
                          label={LETTER_LABELS[index]}
                          size="small"
                          variant="outlined"
                          sx={{ minWidth: 24 }}
                        />
                        <Iconify
                          icon={
                            METRIC_TYPE_ICONS[metric.type] ||
                            "solar:settings-linear"
                          }
                          width={16}
                          sx={{ color: "text.secondary" }}
                        />
                        <ButtonBase
                          onClick={(event) =>
                            openPicker(event, "metric", index)
                          }
                          sx={{
                            flex: 1,
                            minWidth: 0,
                            justifyContent: "flex-start",
                            typography: "s1",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            "&:hover": { color: "primary.main" },
                          }}
                        >
                          {metric.name}
                        </ButtonBase>
                        <CustomTooltip
                          show
                          title="Add filter to this metric"
                          size="small"
                        >
                          <IconButton
                            size="small"
                            aria-label={`Filter ${metric.name}`}
                            onClick={(event) =>
                              openPicker(event, "metric_filter", null, index)
                            }
                            sx={{
                              color: metric.filters?.length
                                ? "primary.main"
                                : "text.secondary",
                            }}
                          >
                            <Iconify icon="solar:filter-linear" width={16} />
                          </IconButton>
                        </CustomTooltip>
                        <IconButton
                          size="small"
                          aria-label={`Remove ${metric.name}`}
                          onClick={() => {
                            setQ({
                              metrics: query.metrics.filter(
                                (_, i) => i !== index,
                              ),
                            });
                            setVisibleSeries(null);
                          }}
                        >
                          <Iconify icon="solar:close-linear" width={14} />
                        </IconButton>
                      </Stack>
                      <AggregationPicker
                        value={metric.aggregation}
                        onChange={(aggregation) =>
                          setQ({
                            metrics: query.metrics.map((item, i) =>
                              i === index ? { ...item, aggregation } : item,
                            ),
                          })
                        }
                        theme={theme}
                        allowedAggregations={
                          def?.allowed ||
                          metric.allowedAggregations ||
                          undefined
                        }
                        extraOptions={
                          def?.category === "evals"
                            ? DATASET_EXTRA_AGGREGATIONS
                            : undefined
                        }
                      />
                      {(metric.filters || []).map((filter, filterIndex) => (
                        <FilterRow
                          key={`${filter.id}-${filterIndex}`}
                          filter={filter}
                          compact
                          onPickAttr={(event) =>
                            openPicker(
                              event,
                              "metric_filter",
                              filterIndex,
                              index,
                            )
                          }
                          onRemove={() => {
                            const metrics = [...query.metrics];
                            metrics[index] = {
                              ...metric,
                              filters: metric.filters.filter(
                                (_, i) => i !== filterIndex,
                              ),
                            };
                            setQ({ metrics });
                          }}
                          onOperator={(operator) =>
                            updateFilter(
                              "metric",
                              filterIndex,
                              { operator },
                              index,
                            )
                          }
                          onPickValues={(event) =>
                            setValuePicker({
                              anchor: event.currentTarget,
                              scope: "metric",
                              index: filterIndex,
                              metricIndex: index,
                            })
                          }
                        />
                      ))}
                    </Box>
                  );
                })}
              </Box>

              <Box>
                <SectionHeader
                  title="Filter"
                  tip="Filter to include or exclude specific calls."
                  onClick={(event) => openPicker(event, "filter")}
                />
                {query.filters.map((filter, index) => (
                  <FilterRow
                    key={`${filter.id}-${index}`}
                    filter={filter}
                    onPickAttr={(event) => openPicker(event, "filter", index)}
                    onRemove={() =>
                      setQ({
                        filters: query.filters.filter((_, i) => i !== index),
                      })
                    }
                    onOperator={(operator) =>
                      updateFilter("global", index, { operator })
                    }
                    onPickValues={(event) =>
                      setValuePicker({
                        anchor: event.currentTarget,
                        scope: "global",
                        index,
                        metricIndex: null,
                      })
                    }
                  />
                ))}
              </Box>

              <Box>
                <SectionHeader
                  title="Breakdown"
                  tip="Split each metric into one series per value."
                  disabled={query.breakdowns.length >= 1}
                  onClick={(event) => openPicker(event, "breakdown")}
                />
                {query.breakdowns.map((breakdown, index) => (
                  <Box key={`${breakdown.id}-${index}`} sx={metricCardSx}>
                    <Stack direction="row" alignItems="center" gap={1}>
                      <Iconify
                        icon={METRIC_TYPE_ICONS.custom_attribute}
                        width={16}
                        sx={{ color: "text.secondary" }}
                      />
                      <ButtonBase
                        onClick={(event) =>
                          openPicker(event, "breakdown", index)
                        }
                        sx={{
                          flex: 1,
                          justifyContent: "flex-start",
                          typography: "s1",
                          "&:hover": { color: "primary.main" },
                        }}
                      >
                        {breakdown.name}
                      </ButtonBase>
                      <IconButton
                        size="small"
                        aria-label={`Remove ${breakdown.name}`}
                        onClick={() => {
                          setQ({ breakdowns: [] });
                          setVisibleSeries(null);
                          if (chartType === "pie") setChartType("column");
                        }}
                      >
                        <Iconify icon="solar:close-linear" width={14} />
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
                onResetAxis={(axis) =>
                  setAxisConfig((previous) => ({
                    ...previous,
                    [axis]:
                      axis === "leftY"
                        ? defaultLeftYAxis()
                        : defaultRightYAxis(),
                  }))
                }
                theme={theme}
              />
            </Box>
          )}
        </Box>
      </Box>

      <RunQueryPicker
        open={picker.open}
        anchorEl={picker.anchor}
        mode={picker.mode}
        categories={
          picker.mode === "metric"
            ? RUN_METRIC_CATEGORIES
            : RUN_ATTRIBUTE_CATEGORIES
        }
        items={pickerItems}
        onSelect={handlePickerSelect}
        onClose={closePicker}
      />
      <RunFilterValuePicker
        anchorEl={valuePicker.anchor}
        values={
          activeValueFilter
            ? distinctAttributeValues(activeValueFilter.id, tasks)
            : []
        }
        selected={activeValueFilter?.value || NO_VALUES}
        onClose={() => setValuePicker(closedValuePicker)}
        onApply={(value) => {
          updateFilter(
            valuePicker.scope,
            valuePicker.index,
            { value },
            valuePicker.metricIndex,
          );
          setValuePicker(closedValuePicker);
        }}
      />
    </Box>
  );
}
