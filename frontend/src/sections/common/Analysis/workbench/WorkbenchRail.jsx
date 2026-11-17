import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  Collapse,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";
import PathInput from "src/sections/common/Values/PathInput";
import { SEGMENTED_SX } from "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection";
import { AGG_LABELS, WB_AGGS, groupLabel, pathKeyOf } from "./workbenchModel";
import { railPlaceholders } from "./railPlaceholders";

export const RAIL_W = 248;

export const CHARTS = [
  { key: "distribution", label: "Distribution", icon: "mdi:chart-histogram" },
  {
    key: "cost_quality",
    label: "Cost vs quality",
    icon: "mdi:chart-scatter-plot",
  },
  { key: "bars", label: "Bars by group", icon: "mdi:chart-bar" },
  { key: "trend", label: "Trend", icon: "mdi:chart-line" },
  { key: "heatmap", label: "Heatmap", icon: "mdi:view-grid-outline" },
  // W5 B5
  { key: "scatter2", label: "Two runs (scatter)", icon: "mdi:chart-bubble" },
  { key: "delta_bars", label: "Delta bars", icon: "mdi:chart-gantt" },
];

/** A collapsible rail section: label + chevron. */
function Section({ label, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        onClick={() => setOpen((o) => !o)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((o) => !o);
          }
        }}
        sx={{ cursor: "pointer", py: 0.5, userSelect: "none" }}
      >
        <Typography typography="s2" color="text.secondary" fontWeight={500}>
          {label}
        </Typography>
        <Iconify
          icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={16}
          sx={{ color: "text.disabled" }}
        />
      </Stack>
      <Collapse in={open}>
        <Box sx={{ pb: 1.25 }}>{children}</Box>
      </Collapse>
    </Box>
  );
}

Section.propTypes = {
  label: PropTypes.string,
  children: PropTypes.node,
  defaultOpen: PropTypes.bool,
};

const inputSx = {
  "& .MuiInputBase-root": { typography: "s1", py: "2px !important" },
};

/** Searchable select over `{key, label, disabled?, chip?, arrow?}` options. */
function RailSelect({
  options,
  value,
  onChange,
  ariaLabel,
  groupBy,
  disabled,
}) {
  const selected = options.find((o) => o.key === value) ?? null;
  return (
    <Autocomplete
      size="small"
      disableClearable
      disabled={disabled}
      options={options}
      value={selected}
      groupBy={groupBy}
      getOptionLabel={(o) => o?.label ?? ""}
      isOptionEqualToValue={(a, b) => a.key === b.key}
      getOptionDisabled={(o) => Boolean(o.disabled)}
      onChange={(_, o) => o && onChange(o.key)}
      renderOption={(props, o) => (
        <li {...props} key={o.key}>
          <CustomTooltip
            show={Boolean(o.disabled)}
            title={o.disabled ?? ""}
            size="small"
            placement="right"
            arrow
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              sx={{ width: "100%" }}
            >
              <Typography typography="s1" sx={{ flex: 1 }} noWrap>
                {o.label}
              </Typography>
              {o.arrow && (
                <Iconify
                  icon={o.arrow === "up" ? "mdi:arrow-up" : "mdi:arrow-down"}
                  width={14}
                  sx={{ color: "text.secondary" }}
                />
              )}
              {o.chip && (
                <Chip
                  label={o.chip}
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 18,
                    typography: "s3",
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
              )}
            </Stack>
          </CustomTooltip>
        </li>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          inputProps={{ ...params.inputProps, "aria-label": ariaLabel }}
          sx={inputSx}
        />
      )}
      slotProps={{ paper: { sx: { minWidth: 260 } } }}
    />
  );
}

RailSelect.propTypes = {
  options: PropTypes.array.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  ariaLabel: PropTypes.string,
  groupBy: PropTypes.func,
  disabled: PropTypes.bool,
};

/**
 * The Workbench rail (R2-SPEC §6.3.1): Scope · Measure · Rows · Columns ·
 * Chart · Compare against; Saved views and export in the footer. Every
 * value lives in the URL; the parent owns it (`onChange(patch)`).
 */
const WorkbenchRail = ({
  state,
  onChange,
  measures,
  measure,
  trust,
  groups,
  columnOptions,
  chartDisabled,
  pathColumns,
  sampleRow,
  exprCheck,
  datasetId,
  runsInView,
  baselineValue,
  pinnedRunId,
  restrictTo,
  onBaseline,
  across,
  byAxis,
  note,
  footer,
}) => {
  const [pathOpen, setPathOpen] = useState(state.rows?.startsWith("path:"));
  const [pathText, setPathText] = useState(
    state.rows?.startsWith("path:")
      ? groupLabel(state.rows).replace(/^Path · /, "")
      : "",
  );
  const [pathError, setPathError] = useState(null);
  // W5 B1: Expression… (an ad-hoc grouping expression, matrixScope.js).
  const isExpr = state.rows?.startsWith("expr:");
  const [exprOpen, setExprOpen] = useState(isExpr);
  const [exprText, setExprText] = useState(isExpr ? state.rows.slice(5) : "");
  const [exprError, setExprError] = useState(null);
  useEffect(() => {
    if (state.rows?.startsWith("expr:")) {
      setExprOpen(true);
      setExprText(state.rows.slice(5));
    }
  }, [state.rows]);
  const applyExpr = (text) => {
    const src = String(text ?? "").trim();
    const err = exprCheck ? exprCheck(src) : null;
    if (err) {
      setExprError(err);
      return;
    }
    setExprError(null);
    onChange({ rows: `expr:${src}` });
  };
  useEffect(() => {
    if (state.rows?.startsWith("path:")) {
      setPathOpen(true);
      setPathText(groupLabel(state.rows).replace(/^Path · /, ""));
    }
  }, [state.rows]);
  const numeric =
    measure && (measure.kind === "numeric" || measure.kind === "score");
  const applyPath = (text) => {
    const key = pathKeyOf(text, pathColumns);
    if (!key) {
      setPathError("Invalid path");
      return;
    }
    setPathError(null);
    onChange({ rows: key });
  };
  const rowsValue =
    across === "datasets"
      ? "dataset"
      : state.rows?.startsWith("path:")
        ? "path"
        : state.rows?.startsWith("expr:")
          ? "expr"
          : state.rows;

  return (
    <Stack
      sx={{
        width: RAIL_W,
        flexShrink: 0,
        borderRight: 1,
        borderColor: "divider",
        bgcolor: "background.paper",
        minHeight: 0,
      }}
    >
      <Box sx={{ flex: 1, overflowY: "auto", px: 1.5, pt: 1 }}>
        {note}
        <Section label="Scope">
          <ToggleButtonGroup
            exclusive
            fullWidth
            size="small"
            value={across}
            onChange={(_, v) => v && onChange({ across: v })}
            sx={SEGMENTED_SX}
          >
            <ToggleButton value="runs">This dataset</ToggleButton>
            <ToggleButton value="datasets">All datasets</ToggleButton>
          </ToggleButtonGroup>
        </Section>
        <Section label="Measure">
          <RailSelect
            ariaLabel="Measure"
            disabled={across === "datasets"}
            options={
              across === "datasets"
                ? [{ key: "headline", label: "Each dataset's headline" }]
                : measures.map((m) => ({
                    key: m.key,
                    label: m.label,
                    disabled: m.disabled,
                    chip: m.evalType,
                    arrow: m.evalType
                      ? m.higherIsBetter
                        ? "up"
                        : "down"
                      : null,
                  }))
            }
            value={across === "datasets" ? "headline" : state.measure}
            onChange={(v) => onChange({ measure: v })}
          />
          {numeric &&
            across !== "datasets" &&
            // W5 B1: two rows of three (Average · Median · p90, then
            // Minimum · Maximum · Std dev); one `agg` value.
            [WB_AGGS.slice(0, 3), WB_AGGS.slice(3)].map((row) => (
              <ToggleButtonGroup
                key={row.join("-")}
                exclusive
                fullWidth
                size="small"
                aria-label="Aggregate"
                value={row.includes(state.agg) ? state.agg : null}
                onChange={(_, v) =>
                  v && onChange({ agg: v === "avg" ? null : v })
                }
                sx={{
                  ...SEGMENTED_SX,
                  mt: 0.75,
                  "& .MuiToggleButton-root": {
                    ...SEGMENTED_SX["& .MuiToggleButton-root"],
                    px: 0.5,
                    whiteSpace: "nowrap",
                  },
                }}
              >
                {row.map((a) => (
                  <ToggleButton key={a} value={a} aria-label={AGG_LABELS[a]}>
                    {AGG_LABELS[a]}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            ))}
          {trust && across !== "datasets" && (
            <Box sx={{ mt: 0.75 }}>{trust}</Box>
          )}
        </Section>
        {/* W5 B2: a run metric is one number per run; rows, columns and
            the chart kind do not apply (its panel picks its own chart). */}
        {measure?.kind !== "run_metric" && (
          <>
            <Section label="Rows: group by">
              <RailSelect
                ariaLabel="Rows: group by"
                options={groups}
                value={rowsValue}
                disabled={across === "datasets"}
                onChange={(v) => {
                  if (v === "path") {
                    setPathOpen(true);
                    setExprOpen(false);
                    return;
                  }
                  if (v === "expr") {
                    setExprOpen(true);
                    setPathOpen(false);
                    return;
                  }
                  setPathOpen(false);
                  setExprOpen(false);
                  // `none` is written to the URL: with no `rows=` the Workbench
                  // groups by Category (defaultRowsAxis).
                  onChange({ rows: v });
                }}
              />
              {(pathOpen || rowsValue === "path") && across !== "datasets" && (
                <Box sx={{ mt: 0.75 }}>
                  <PathInput
                    value={pathText}
                    onChange={(v) => {
                      setPathText(v);
                      setPathError(null);
                    }}
                    columns={pathColumns}
                    sampleRow={sampleRow}
                    placeholder={railPlaceholders(pathColumns).path}
                    error={pathError ?? undefined}
                    onEnter={applyPath}
                  />
                  <Button
                    size="small"
                    sx={{ mt: 0.5 }}
                    onClick={() => applyPath(pathText)}
                    disabled={!pathText}
                  >
                    Group by this path
                  </Button>
                </Box>
              )}
              {exprCheck &&
                (exprOpen || rowsValue === "expr") &&
                across !== "datasets" && (
                  <Box sx={{ mt: 0.75 }}>
                    <TextField
                      size="small"
                      fullWidth
                      multiline
                      maxRows={4}
                      value={exprText}
                      onChange={(e) => {
                        setExprText(e.target.value);
                        setExprError(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          applyExpr(exprText);
                        }
                      }}
                      placeholder={railPlaceholders(pathColumns).expression}
                      inputProps={{
                        "aria-label": "Group by expression",
                        spellCheck: false,
                        style: { fontFamily: "monospace", fontSize: 12 },
                      }}
                      error={Boolean(exprError)}
                      helperText={
                        exprError ??
                        "Each row goes in the group of its value. Row fields: columns, metadata.<key>, split, tags, row."
                      }
                      FormHelperTextProps={{ sx: { mx: 0, typography: "s3" } }}
                    />
                    <Button
                      size="small"
                      sx={{ mt: 0.5 }}
                      onClick={() => applyExpr(exprText)}
                      disabled={!exprText.trim()}
                    >
                      Group by this expression
                    </Button>
                  </Box>
                )}
            </Section>
            <Section label="Columns">
              <RailSelect
                ariaLabel="Columns"
                options={columnOptions}
                value={across === "datasets" ? byAxis : state.cols}
                onChange={(v) =>
                  onChange(
                    across === "datasets"
                      ? { bycols: v }
                      : { cols: v === "run" ? null : v },
                  )
                }
              />
            </Section>
            <Section label="Chart">
              <ToggleButtonGroup
                exclusive
                size="small"
                value={state.chart}
                onChange={(_, v) => v && onChange({ chart: v })}
                sx={{
                  ...SEGMENTED_SX,
                  // W5 B5: seven kinds wrap onto two lines in the rail.
                  flexWrap: "wrap",
                  "& .MuiToggleButton-root": {
                    ...SEGMENTED_SX["& .MuiToggleButton-root"],
                    px: 1,
                  },
                }}
              >
                {CHARTS.map((c) => {
                  const reason = chartDisabled?.[c.key];
                  return (
                    <ToggleButton
                      key={c.key}
                      value={c.key}
                      disabled={Boolean(reason)}
                      aria-label={c.label}
                      sx={{ "&.Mui-disabled": { pointerEvents: "auto" } }}
                    >
                      <CustomTooltip
                        show
                        title={reason ? `${c.label} · ${reason}` : c.label}
                        size="small"
                        placement="top"
                        arrow
                      >
                        <Box sx={{ display: "flex" }}>
                          <Iconify icon={c.icon} width={18} />
                        </Box>
                      </CustomTooltip>
                    </ToggleButton>
                  );
                })}
              </ToggleButtonGroup>
            </Section>
          </>
        )}
        <Section label="Compare against">
          {across === "datasets" ? (
            <Typography typography="s3" color="text.secondary">
              Columns compare configs; each row is one dataset.
            </Typography>
          ) : (
            <Box
              sx={{
                overflow: "hidden",
                "& .MuiButton-root": {
                  maxWidth: "100%",
                  justifyContent: "flex-start",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                  textOverflow: "ellipsis",
                },
              }}
            >
              <BaselinePicker
                datasetId={datasetId}
                value={baselineValue}
                pinnedRunId={pinnedRunId}
                runsInView={runsInView}
                restrictTo={restrictTo}
                allowNone
                onChange={(id) => onBaseline(id)}
              />
            </Box>
          )}
        </Section>
      </Box>
      <Box sx={{ borderTop: 1, borderColor: "divider", px: 1.5, py: 1 }}>
        {footer}
      </Box>
    </Stack>
  );
};

WorkbenchRail.propTypes = {
  state: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  measures: PropTypes.array.isRequired,
  measure: PropTypes.object,
  trust: PropTypes.node,
  groups: PropTypes.array.isRequired,
  columnOptions: PropTypes.array.isRequired,
  chartDisabled: PropTypes.object,
  pathColumns: PropTypes.array,
  sampleRow: PropTypes.object,
  exprCheck: PropTypes.func,
  datasetId: PropTypes.string,
  runsInView: PropTypes.array,
  baselineValue: PropTypes.string,
  pinnedRunId: PropTypes.string,
  restrictTo: PropTypes.array,
  onBaseline: PropTypes.func,
  across: PropTypes.string,
  byAxis: PropTypes.string,
  note: PropTypes.node,
  footer: PropTypes.node,
};

export default WorkbenchRail;
