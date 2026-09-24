/* eslint-disable react/prop-types */
/**
 * Widget editor building blocks shared by the Observe dashboards editor and
 * the Simulate run-analytics widget editor, so both surfaces render the same
 * pickers, toggles, axis settings and series colours.
 * Extracted verbatim from WidgetEditorView.jsx.
 */
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  ClickAwayListener,
  Divider,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  MenuItem,
  Paper,
  Popper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  AGGREGATION_OPTIONS,
  ALL_AGGREGATIONS,
  PERCENTILE_OPTIONS,
} from "./constants";
import { DEFAULT_DECIMALS, formatValueWithConfig } from "./widgetUtils";

export const AXIS_LABEL_MAX_LENGTH = 50;

export const CHART_TYPES = [
  { label: "Line", value: "line", icon: "mdi:chart-line", group: "line" },
  {
    label: "Stacked Line",
    value: "stacked_line",
    icon: "mdi:chart-line-stacked",
    group: "line",
  },
  { label: "Column", value: "column", icon: "mdi:chart-bar", group: "column" },
  {
    label: "Stacked Column",
    value: "stacked_column",
    icon: "mdi:chart-bar-stacked",
    group: "column",
  },
  {
    label: "Bar",
    value: "bar",
    icon: "mdi:chart-timeline-variant-shimmer",
    group: "bar",
  },
  {
    label: "Stacked Bar",
    value: "stacked_bar",
    icon: "mdi:chart-timeline-variant-shimmer",
    group: "bar",
  },
  { label: "Pie", value: "pie", icon: "mdi:chart-pie", group: "other" },
  { label: "Table", value: "table", icon: "mdi:table", group: "other" },
  { label: "Metric", value: "metric", icon: "mdi:pound", group: "other" },
];

export const UNIT_PRESETS = [
  { label: "$", value: "$" },
  { label: "%", value: "%" },
  { label: "#", value: "#" },
  { label: "ms", value: "ms" },
  { label: "s", value: "s" },
  { label: "tokens", value: "tokens" },
  { label: "cents", value: "cents" },
  { label: "wpm", value: "wpm" },
  { label: "/min", value: "/min" },
];

export const DATASET_EXTRA_AGGREGATIONS = [
  { label: "Pass Rate", value: "pass_rate" },
  { label: "Fail Rate", value: "fail_rate" },
  { label: "Pass Count", value: "pass_count" },
  { label: "Fail Count", value: "fail_count" },
  { label: "True Rate", value: "true_rate" },
];

export const METRIC_TYPE_ICONS = {
  system: "mdi:cog-outline",
  eval_metric: "mdi:check-circle-outline",
  annotation: "mdi:format-quote-close",
  custom_attribute: "mdi:tune-variant",
  custom_column: "mdi:table-column",
};

export const SERIES_COLORS = [
  "#7B56DB", // purple (primary)
  "#1ABCFE", // cyan
  "#FF6B6B", // coral red
  "#2ECB71", // emerald green
  "#F7B731", // amber
  "#E84393", // magenta pink
  "#0984E3", // ocean blue
  "#FD7E14", // tangerine orange
  "#00CEC9", // teal
  "#A29BFE", // lavender
];

export const hashSeriesName = (name) => {
  const s = String(name || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
};
export const buildSeriesColorMap = (names) => {
  const map = {};
  const used = new Set();
  (names || []).forEach((name) => {
    const start = hashSeriesName(name) % SERIES_COLORS.length;
    let picked = start;
    for (let i = 0; i < SERIES_COLORS.length; i += 1) {
      const candidate = (start + i) % SERIES_COLORS.length;
      if (!used.has(candidate)) {
        picked = candidate;
        break;
      }
    }
    used.add(picked);
    map[name] = SERIES_COLORS[picked];
  });
  return map;
};
export const getSeriesColor = (name, map) =>
  (map && map[name]) ||
  SERIES_COLORS[hashSeriesName(name) % SERIES_COLORS.length];

export const LETTER_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function ToggleButtons({ options, value, onChange, theme }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      {options.map((opt, i) => (
        <Box
          key={opt.value}
          onClick={() => onChange(opt.value)}
          sx={{
            px: 1.5,
            py: 0.5,
            cursor: "pointer",
            fontSize: "13px",
            fontWeight: value === opt.value ? 600 : 400,
            color:
              value === opt.value
                ? theme.palette.text.primary
                : theme.palette.text.secondary,
            bgcolor:
              value === opt.value
                ? theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.08)"
                  : "rgba(0,0,0,0.06)"
                : "transparent",
            borderRight:
              i < options.length - 1
                ? `1px solid ${theme.palette.divider}`
                : "none",
            whiteSpace: "nowrap",
            userSelect: "none",
            transition: "all 0.15s",
            "&:hover": {
              bgcolor:
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.05)"
                  : "rgba(0,0,0,0.03)",
            },
          }}
        >
          {opt.label}
        </Box>
      ))}
    </Box>
  );
}

ToggleButtons.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.node.isRequired,
      value: PropTypes.any,
    }),
  ).isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
};

export function AxisSection({ title, config, onChange, theme, showReset, onReset }) {
  return (
    <Box sx={{ mb: 3 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="subtitle2" fontWeight={700}>
          {title}
        </Typography>
        {showReset && (
          <Typography
            variant="caption"
            sx={{
              cursor: "pointer",
              color: "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
            onClick={onReset}
          >
            Reset
          </Typography>
        )}
      </Stack>

      {/* Axis Visible/Hidden */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Axis
        </Typography>
        <ToggleButtons
          options={[
            { label: "Visible", value: true },
            { label: "Hidden", value: false },
          ]}
          value={config.visible}
          onChange={(v) => onChange("visible", v)}
          theme={theme}
        />
      </Stack>

      {/* Label */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Label
        </Typography>
        <TextField
          size="small"
          value={config.label}
          onChange={(e) => onChange("label", e.target.value)}
          placeholder="e.g. Cost ($)"
          inputProps={{ maxLength: AXIS_LABEL_MAX_LENGTH }}
          sx={{ width: 180, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
        />
      </Stack>

      {/* Unit */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Unit
        </Typography>
        <TextField
          select
          size="small"
          value={
            UNIT_PRESETS.some((u) => u.value === config.unit)
              ? config.unit
              : "custom"
          }
          onChange={(e) =>
            onChange("unit", e.target.value === "custom" ? "" : e.target.value)
          }
          sx={{ width: 180, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
        >
          {UNIT_PRESETS.map((opt) => (
            <MenuItem
              key={opt.value}
              value={opt.value}
              sx={{ fontSize: "13px" }}
            >
              {opt.label}
            </MenuItem>
          ))}
          <MenuItem value="custom" sx={{ fontSize: "13px" }}>
            Custom
          </MenuItem>
        </TextField>
      </Stack>

      {/* Prefix / Suffix */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Prefix / Suffix
        </Typography>
        <ToggleButtons
          options={[
            { label: "Prefix", value: "prefix" },
            { label: "Suffix", value: "suffix" },
          ]}
          value={config.prefixSuffix}
          onChange={(v) => onChange("prefixSuffix", v)}
          theme={theme}
        />
      </Stack>

      {/* Abbreviation */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Abbreviation
        </Typography>
        <ToggleButtons
          options={[
            { label: "Yes", value: true },
            { label: "No", value: false },
          ]}
          value={config.abbreviation}
          onChange={(v) => onChange("abbreviation", v)}
          theme={theme}
        />
      </Stack>

      {/* Decimals */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Decimals
        </Typography>
        <ToggleButtons
          options={[
            {
              label: "\u2190 .0",
              value: Math.max(0, (config.decimals ?? DEFAULT_DECIMALS) - 1),
            },
            {
              label: ".00 \u2192",
              value: (config.decimals ?? DEFAULT_DECIMALS) + 1,
            },
          ]}
          value={null}
          onChange={(v) => onChange("decimals", Math.max(0, Math.min(6, v)))}
          theme={theme}
        />
      </Stack>

      {/* Preview */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Preview
        </Typography>
        <Typography variant="body2" fontWeight={500}>
          {(() => {
            const sample = 1250000;
            return formatValueWithConfig(sample, config, {
              fallbackDecimals: DEFAULT_DECIMALS,
            });
          })()}
        </Typography>
      </Stack>

      {/* Threshold Bounds */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Threshold Bounds
        </Typography>
        <Stack direction="row" gap={0.5}>
          <TextField
            size="small"
            value={config.min}
            onChange={(e) => onChange("min", e.target.value)}
            placeholder="Min"
            sx={{ width: 80, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
          />
          <TextField
            size="small"
            value={config.max}
            onChange={(e) => onChange("max", e.target.value)}
            placeholder="Max"
            sx={{ width: 80, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
          />
        </Stack>
      </Stack>

      {/* Out of Bounds */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Out of Bounds
        </Typography>
        <ToggleButtons
          options={[
            { label: "Visible", value: "visible" },
            { label: "Hidden", value: "hidden" },
          ]}
          value={config.outOfBounds}
          onChange={(v) => onChange("outOfBounds", v)}
          theme={theme}
        />
      </Stack>

      {/* Scale */}
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="body2" color="text.secondary">
          Scale
        </Typography>
        <ToggleButtons
          options={[
            { label: "Linear", value: "linear" },
            { label: "Logarithmic", value: "logarithmic" },
          ]}
          value={config.scale}
          onChange={(v) => onChange("scale", v)}
          theme={theme}
        />
      </Stack>
    </Box>
  );
}

AxisSection.propTypes = {
  title: PropTypes.string.isRequired,
  config: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
  showReset: PropTypes.bool,
  onReset: PropTypes.func,
};

export function AggregationPicker({
  value,
  onChange,
  theme,
  extraOptions,
  allowedAggregations,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [showPercentiles, setShowPercentiles] = useState(false);

  const handleOpen = (e) => {
    setAnchorEl(e.currentTarget);
    setShowPercentiles(false);
  };

  const handleClose = () => {
    setAnchorEl(null);
    setShowPercentiles(false);
  };

  const handleSelect = (val) => {
    onChange(val);
    handleClose();
  };

  const allAggs = extraOptions
    ? [...ALL_AGGREGATIONS, ...extraOptions]
    : ALL_AGGREGATIONS;
  const allowedSet = allowedAggregations?.length
    ? new Set(allowedAggregations)
    : null;
  const primaryAggs = allowedSet
    ? AGGREGATION_OPTIONS.filter((opt) => allowedSet.has(opt.value))
    : AGGREGATION_OPTIONS;
  const allowedExtraOptions = allowedSet
    ? (extraOptions || []).filter((opt) => allowedSet.has(opt.value))
    : extraOptions;
  const percentileAggs = allowedSet
    ? PERCENTILE_OPTIONS.filter((opt) => allowedSet.has(opt.value))
    : PERCENTILE_OPTIONS;
  const visibleAggs = allowedSet
    ? [...primaryAggs, ...(allowedExtraOptions || []), ...percentileAggs]
    : allAggs;
  const current = visibleAggs.find((a) => a.value === value);
  const open = Boolean(anchorEl);

  return (
    <>
      <Chip
        label={current?.label || value}
        size="small"
        variant="outlined"
        onClick={handleOpen}
        deleteIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onDelete={handleOpen}
        sx={{ mt: 1, cursor: "pointer", fontSize: "12px" }}
      />
      <Popper
        open={open}
        anchorEl={anchorEl}
        placement="bottom-start"
        sx={{ zIndex: 1400 }}
      >
        <ClickAwayListener onClickAway={handleClose}>
          <Paper
            elevation={8}
            sx={{
              minWidth: 180,
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            {!showPercentiles ? (
              <List dense disablePadding>
                {primaryAggs.map((opt) => (
                  <ListItemButton
                    key={opt.value}
                    selected={value === opt.value}
                    onClick={() => handleSelect(opt.value)}
                    sx={{ py: 0.75 }}
                  >
                    <ListItemText
                      primary={opt.label}
                      primaryTypographyProps={{
                        variant: "body2",
                        fontSize: "13px",
                      }}
                    />
                  </ListItemButton>
                ))}
                {allowedExtraOptions && allowedExtraOptions.length > 0 && (
                  <>
                    <Divider />
                    {allowedExtraOptions.map((opt) => (
                      <ListItemButton
                        key={opt.value}
                        selected={value === opt.value}
                        onClick={() => handleSelect(opt.value)}
                        sx={{ py: 0.75 }}
                      >
                        <ListItemText
                          primary={opt.label}
                          primaryTypographyProps={{
                            variant: "body2",
                            fontSize: "13px",
                          }}
                        />
                      </ListItemButton>
                    ))}
                  </>
                )}
                {percentileAggs.length > 0 && (
                  <>
                    <Divider />
                    <ListItemButton
                      onClick={() => setShowPercentiles(true)}
                      sx={{ py: 0.75 }}
                    >
                      <ListItemText
                        primary="Percentile"
                        primaryTypographyProps={{
                          variant: "body2",
                          fontSize: "13px",
                          fontWeight: percentileAggs.some(
                            (p) => p.value === value,
                          )
                            ? 600
                            : 400,
                        }}
                      />
                      <Iconify
                        icon="mdi:chevron-right"
                        width={16}
                        sx={{ color: "text.secondary" }}
                      />
                    </ListItemButton>
                  </>
                )}
              </List>
            ) : (
              <List dense disablePadding>
                <ListItemButton
                  onClick={() => setShowPercentiles(false)}
                  sx={{ py: 0.75 }}
                >
                  <Iconify
                    icon="mdi:chevron-left"
                    width={16}
                    sx={{ color: "text.secondary", mr: 0.5 }}
                  />
                  <ListItemText
                    primary="Percentile"
                    primaryTypographyProps={{
                      variant: "body2",
                      fontSize: "13px",
                      fontWeight: 600,
                    }}
                  />
                </ListItemButton>
                <Divider />
                {percentileAggs.map((opt) => (
                  <ListItemButton
                    key={opt.value}
                    selected={value === opt.value}
                    onClick={() => handleSelect(opt.value)}
                    sx={{ py: 0.75 }}
                  >
                    <ListItemText
                      primary={opt.label}
                      primaryTypographyProps={{
                        variant: "body2",
                        fontSize: "13px",
                      }}
                    />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Paper>
        </ClickAwayListener>
      </Popper>
    </>
  );
}

AggregationPicker.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
  extraOptions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ),
  allowedAggregations: PropTypes.arrayOf(PropTypes.string),
};

/* ── Axis defaults ─────────────────────────────────────────────────── */

export const defaultLeftYAxis = () => ({
  visible: true,
  label: "",
  unit: "",
  prefixSuffix: "prefix",
  abbreviation: true,
  decimals: DEFAULT_DECIMALS,
  min: "",
  max: "",
  outOfBounds: "visible",
  scale: "linear",
});

export const defaultRightYAxis = () => ({
  visible: false,
  label: "",
  unit: "",
  prefixSuffix: "prefix",
  abbreviation: true,
  decimals: DEFAULT_DECIMALS,
  min: "",
  max: "",
  outOfBounds: "hidden",
  scale: "linear",
});

export const defaultAxisConfig = () => ({
  leftY: defaultLeftYAxis(),
  rightY: defaultRightYAxis(),
  xAxis: { visible: true, label: "" },
  seriesAxis: {}, // { [seriesIndex]: "left" | "right" }
});

/* ── Preset pill group (time range / run range) ────────────────────── */

export function PresetPillGroup({ options, value, onSelect, theme, renderLabel, itemRef, isDisabled, titleFor }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: 1,
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      {options.map((p, i) => {
        const disabled = !!isDisabled?.(p.value);
        return (
        <Box
          key={p.value}
          ref={itemRef ? itemRef(p.value) : undefined}
          onClick={disabled ? undefined : () => onSelect(p.value)}
          title={titleFor?.(p.value) || undefined}
          aria-disabled={disabled || undefined}
          sx={{
            px: 1.5,
            py: 0.6,
            cursor: disabled ? "default" : "pointer",
            opacity: disabled ? 0.4 : 1,
            fontSize: "13px",
            fontWeight: value === p.value ? 600 : 400,
            color:
              value === p.value
                ? theme.palette.text.primary
                : theme.palette.text.secondary,
            bgcolor:
              value === p.value
                ? theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.08)"
                  : "rgba(0,0,0,0.06)"
                : "transparent",
            borderRight:
              i < options.length - 1
                ? `1px solid ${theme.palette.divider}`
                : "none",
            whiteSpace: "nowrap",
            userSelect: "none",
            transition: "all 0.15s",
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            "&:hover": {
              bgcolor:
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.05)"
                  : "rgba(0,0,0,0.03)",
            },
          }}
        >
          {renderLabel ? renderLabel(p) : p.label}
        </Box>
        );
      })}
    </Box>
  );
}

PresetPillGroup.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.node, value: PropTypes.any }),
  ).isRequired,
  value: PropTypes.any,
  onSelect: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
  renderLabel: PropTypes.func,
  itemRef: PropTypes.func,
  /* Optional: grey out an option (it stays visible, not clickable). */
  isDisabled: PropTypes.func,
  titleFor: PropTypes.func,
};

/* ── View mode bar on the chart/table splitter ─────────────────────── */

export const VIEW_MODES = [
  { mode: "table", icon: "mdi:table", height: 0, tip: "Table" },
  { mode: "split-table", icon: "mdi:page-layout-header", height: 180, tip: "Chart + Table" },
  { mode: "split-chart", icon: "mdi:page-layout-body", height: 350, tip: "Chart (expanded)" },
  { mode: "chart", icon: "mdi:chart-line", height: 600, tip: "Chart only" },
];

export function ViewModeBar({ viewMode, onSelect, onDragStart, isDragging, theme }) {
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
        "&:hover .divider-line": {
          bgcolor: "primary.main",
          opacity: 1,
        },
      }}
    >
      {/* Divider line — subtle by default, colored on hover */}
      <Box
        className="divider-line"
        sx={{
          position: "absolute",
          left: 0,
          right: 0,
          top: "50%",
          transform: "translateY(-50%)",
          height: 3,
          bgcolor: "divider",
          opacity: 1,
          transition: "background-color 0.15s, opacity 0.15s",
          ...(isDragging && {
            bgcolor: "primary.main",
            opacity: 1,
          }),
        }}
      />
      {/* Toggle buttons on top of the line */}
      <Stack
        direction="row"
        sx={{
          position: "relative",
          zIndex: 1,
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: "8px",
          overflow: "hidden",
          bgcolor: "background.paper",
          boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
        }}
      >
        {VIEW_MODES.map(({ mode, icon, height, tip }) => (
          <Tooltip key={mode} title={tip} placement="top">
            <IconButton
              size="small"
              aria-label={tip}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(mode, height);
              }}
              onMouseDown={(e) => e.stopPropagation()}
              sx={{
                borderRadius: 0,
                px: 1.2,
                py: 0.6,
                bgcolor: viewMode === mode ? "action.selected" : "transparent",
                "&:hover": {
                  bgcolor: viewMode === mode ? "action.selected" : "action.hover",
                },
              }}
            >
              <Iconify
                icon={icon}
                width={18}
                sx={{ color: viewMode === mode ? "text.primary" : "text.disabled" }}
              />
            </IconButton>
          </Tooltip>
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
  theme: PropTypes.object.isRequired,
};

/* ── Chart tab: axis assignment + left / right Y + X axis ──────────── */

export function ChartAxisSettings({
  chartKind,
  series,
  colorFor,
  axisConfig,
  onUpdateAxis,
  onSetSeriesAxis,
  onResetAxis,
  theme,
}) {
  if (chartKind === "pie" || chartKind === "table" || chartKind === "metric") {
    return (
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ fontStyle: "italic", textAlign: "center", mt: 4 }}
      >
        {chartKind === "pie"
          ? "Pie charts do not have axis settings"
          : chartKind === "table"
            ? "Table view does not have axis settings"
            : "Metric cards do not have axis settings"}
      </Typography>
    );
  }
  return (
    <>
      <Typography
        variant="overline"
        fontWeight={700}
        sx={{ mb: 2, display: "block", letterSpacing: 1.5 }}
      >
        AXIS
      </Typography>
      <Box>
        <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
          Axis Assignment
        </Typography>
        {series.map((s, si) => {
          const seriesColor = colorFor(s.name);
          return (
            <Stack
              key={si}
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{ mb: 1 }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                sx={{ flex: 1, minWidth: 0 }}
              >
                <Box
                  sx={{
                    width: 22,
                    height: 22,
                    borderRadius: 0.5,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    bgcolor: seriesColor + "22",
                    color: seriesColor,
                    fontSize: "11px",
                    fontWeight: 700,
                  }}
                >
                  {LETTER_LABELS[si] || si}
                </Box>
                <Iconify
                  icon="mdi:chart-line"
                  width={16}
                  sx={{ color: seriesColor, flexShrink: 0 }}
                />
                <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
                  {s.name?.split(" (")[0] || s.name}
                </Typography>
              </Stack>
              <ToggleButtons
                options={[
                  { label: "L", value: "left" },
                  { label: "R", value: "right" },
                ]}
                value={axisConfig.seriesAxis[si] || "left"}
                onChange={(v) => onSetSeriesAxis(si, v)}
                theme={theme}
              />
            </Stack>
          );
        })}
        {series.length === 0 && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontStyle: "italic" }}
          >
            Add metrics to see axis assignments
          </Typography>
        )}
      </Box>
      <Divider sx={{ my: 2 }} />
      <AxisSection
        title="Left Y-Axis"
        config={axisConfig.leftY}
        onChange={(key, val) => onUpdateAxis("leftY", key, val)}
        theme={theme}
        showReset
        onReset={() => onResetAxis("leftY")}
      />
      <Divider sx={{ my: 2 }} />
      <AxisSection
        title="Right Y-Axis"
        config={axisConfig.rightY}
        onChange={(key, val) => onUpdateAxis("rightY", key, val)}
        theme={theme}
        showReset
        onReset={() => onResetAxis("rightY")}
      />
      <Divider sx={{ my: 2 }} />
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
          X-Axis
        </Typography>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: 1.5 }}
        >
          <Typography variant="body2" color="text.secondary">
            Axis
          </Typography>
          <ToggleButtons
            options={[
              { label: "Visible", value: true },
              { label: "Hidden", value: false },
            ]}
            value={axisConfig.xAxis.visible}
            onChange={(v) => onUpdateAxis("xAxis", "visible", v)}
            theme={theme}
          />
        </Stack>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Typography variant="body2" color="text.secondary">
            Label
          </Typography>
          <TextField
            size="small"
            value={axisConfig.xAxis.label}
            onChange={(e) => onUpdateAxis("xAxis", "label", e.target.value)}
            placeholder="e.g. Time (s)"
            inputProps={{ maxLength: AXIS_LABEL_MAX_LENGTH }}
            sx={{
              width: 180,
              "& .MuiOutlinedInput-root": { fontSize: "13px" },
            }}
          />
        </Stack>
      </Box>
    </>
  );
}

ChartAxisSettings.propTypes = {
  chartKind: PropTypes.oneOf(["pie", "table", "metric", null]),
  series: PropTypes.array.isRequired,
  colorFor: PropTypes.func.isRequired,
  axisConfig: PropTypes.object.isRequired,
  onUpdateAxis: PropTypes.func.isRequired,
  onSetSeriesAxis: PropTypes.func.isRequired,
  onResetAxis: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
};
