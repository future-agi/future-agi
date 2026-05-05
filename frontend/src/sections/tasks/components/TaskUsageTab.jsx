/* eslint-disable react/prop-types */
import {
  Box,
  Chip,
  IconButton,
  MenuItem,
  Select,
  Skeleton,
  Slide,
  Tooltip,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import Editor from "@monaco-editor/react";
import { DataTable, DataTablePagination } from "src/components/data-table";
import Iconify from "src/components/iconify";
import { useSettingsContext } from "src/components/settings/context";
import DateTimeRangePicker from "src/sections/projects/DateTimeRangePicker";

import { useTaskUsageChart, useTaskUsageLogs } from "../hooks/useTaskUsage";
import UsageChart from "src/sections/evals/components/UsageChart";
import { JsonValueTree } from "src/sections/evals/components/DatasetTestMode";
import { classifyTaskError } from "src/sections/common/EvalsTasks/classifyTaskError";

// ── Inline stat ──
const StatPill = ({ label, value, color }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ fontSize: "11px" }}
    >
      {label}:
    </Typography>
    <Typography
      variant="caption"
      fontWeight={700}
      color={color}
      sx={{ fontSize: "12px" }}
    >
      {value}
    </Typography>
  </Box>
);

// ── Map date picker option to API period param ──
// Tasks may run over months, so we extend the eval-usage map with the
// "6M" and "12M" picker options (added to DateTimeRangePicker for the
// task flow). Without these entries the lookup falls through to 30d
// and the picker silently does nothing on those clicks.
const DATE_OPTION_TO_PERIOD = {
  "30 mins": "30m",
  "6 hrs": "6h",
  Today: "1d",
  Yesterday: "1d",
  "7D": "7d",
  "30D": "30d",
  "3M": "90d",
  "6M": "180d",
  "12M": "365d",
  Custom: "30d",
};

// ── Score chip ──
const ScoreCell = ({ value }) => {
  if (value == null)
    return (
      <Typography
        variant="body2"
        color="text.disabled"
        sx={{ fontSize: "12px" }}
      >
        —
      </Typography>
    );
  if (typeof value === "number")
    return (
      <Chip
        label={value.toFixed(2)}
        size="small"
        color={value >= 0.7 ? "success" : value >= 0.3 ? "warning" : "error"}
        sx={{ fontSize: "11px", height: 20, fontWeight: 600 }}
      />
    );
  return (
    <Chip
      label={String(value)}
      size="small"
      color="default"
      sx={{ fontSize: "11px", height: 20 }}
    />
  );
};

// ── Columns ──
// Lean version of EvalUsageTab columns. Tasks add an "Eval" chip column
// since a task can run multiple evals (eval-usage tab is implicitly
// scoped to one template, so it doesn't need this).
const useColumns = () =>
  useMemo(
    () => [
      {
        id: "indicator",
        accessorKey: "score",
        header: "",
        size: 4,
        enableSorting: false,
        cell: ({ getValue, row }) => {
          const v = getValue();
          const isError = row.original.status === "error";
          const color = isError
            ? "error.main"
            : v == null
              ? "transparent"
              : typeof v === "number"
                ? v >= 0.7
                  ? "success.main"
                  : v >= 0.3
                    ? "warning.main"
                    : "error.main"
                : "text.disabled";
          return (
            <Box
              sx={{
                width: 3,
                height: 28,
                borderRadius: 1,
                backgroundColor: color,
              }}
            />
          );
        },
      },
      {
        id: "score",
        accessorKey: "score",
        header: "Score",
        size: 80,
        cell: ({ getValue }) => <ScoreCell value={getValue()} />,
      },
      {
        id: "result",
        accessorKey: "result",
        header: "Result",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return null;
          const isPassed = v === "Passed" || v === "Pass";
          const isFailed = v === "Failed" || v === "Fail";
          const isError = v === "Error";
          return (
            <Chip
              label={v}
              size="small"
              color={
                isPassed ? "success" : isFailed || isError ? "error" : "default"
              }
              variant="outlined"
              sx={{ fontSize: "11px", height: 20 }}
            />
          );
        },
      },
      {
        id: "eval_name",
        accessorKey: "eval_name",
        header: "Eval",
        size: 140,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return null;
          return (
            <Tooltip title={v}>
              <Chip
                label={v}
                size="small"
                variant="outlined"
                sx={{
                  fontSize: "10px",
                  height: 18,
                  maxWidth: 130,
                  "& .MuiChip-label": {
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  },
                }}
              />
            </Tooltip>
          );
        },
      },
      {
        id: "input",
        accessorKey: "input",
        header: "Input",
        meta: { flex: 2 },
        minSize: 200,
        cell: ({ getValue }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{ fontSize: "12px", color: "text.secondary" }}
          >
            {getValue() || "—"}
          </Typography>
        ),
      },
      {
        id: "reason",
        accessorKey: "reason",
        header: "Reason",
        meta: { flex: 1.5 },
        minSize: 150,
        cell: ({ getValue }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{
              fontSize: "12px",
              color: "text.secondary",
              fontStyle: "italic",
            }}
          >
            {getValue() || "—"}
          </Typography>
        ),
      },
      {
        id: "createdAt",
        accessorKey: "created_at",
        header: "Ran at",
        size: 140,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return null;
          const d = new Date(v);
          return (
            <Typography
              variant="body2"
              noWrap
              sx={{ fontSize: "11px", color: "text.disabled" }}
            >
              {d.toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
              ,{" "}
              {d.toLocaleTimeString(undefined, {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </Typography>
          );
        },
      },
    ],
    [],
  );

// ── Detail row ──
// Lightweight wrapper around JsonValueTree that owns its own
// expand/collapse state, so each row in the panel keeps its tree state
// independently as the user clicks around.
const ExpandableJson = ({ value }) => {
  const [expanded, setExpanded] = useState(false);
  return (
    <JsonValueTree
      value={value}
      expanded={expanded}
      onToggle={() => setExpanded((v) => !v)}
    />
  );
};

ExpandableJson.propTypes = {
  // Value can be anything — string, number, dict, list, null. The
  // JsonValueTree component handles each kind.
  // eslint-disable-next-line react/forbid-prop-types
  value: PropTypes.any,
};

const DetailRow = ({ label, value, color, chip, chipColor, mono }) => {
  // If value is a non-null object/array, render it through JsonValueTree
  // so users can drill into nested keys (e.g. `prompt.messages.0.content`)
  // instead of seeing "[object Object]". Strings, numbers, booleans, and
  // null still render as plain text.
  const isJsonValue =
    !chip && value !== null && value !== undefined && typeof value === "object";

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <CustomTooltip
        show
        title={label}
        placement="top-start"
        enterDelay={300}
        size="small"
      >
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            width: 90,
            flexShrink: 0,
            pt: 0.25,
            pr: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </Typography>
      </CustomTooltip>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {chip ? (
          <Chip
            label={value}
            size="small"
            color={chipColor || "default"}
            variant="outlined"
            sx={{ fontSize: "11px", height: 20 }}
          />
        ) : isJsonValue ? (
          <ExpandableJson value={value} />
        ) : (
          <Typography
            variant="body2"
            sx={{
              fontSize: "12px",
              color: color || "text.primary",
              fontFamily: mono ? "monospace" : "inherit",
              wordBreak: "break-word",
            }}
          >
            {value}
          </Typography>
        )}
      </Box>
    </Box>
  );
};

DetailRow.propTypes = {
  label: PropTypes.string.isRequired,
  // eslint-disable-next-line react/forbid-prop-types
  value: PropTypes.any,
  color: PropTypes.string,
  chip: PropTypes.bool,
  chipColor: PropTypes.string,
  mono: PropTypes.bool,
};

// ── Error details — structured display for failed eval rows ──
// Uses the same `classifyTaskError` helper as the Logs tab so error
// rows in the side drawer get the same actionable formatting:
//   - Strip the redundant "Error during evaluation:" prefix
//   - Show a friendly title (e.g. "Missing attribute on spans")
//   - Show the normalized message in a monospace block
//   - List the actionable hints from the classifier
//   - Keep the full raw error in a collapsed details for support copy-paste
const ErrorDetails = ({ rawError }) => {
  const [showRaw, setShowRaw] = useState(false);
  const classified = useMemo(
    () => classifyTaskError(rawError || ""),
    [rawError],
  );
  if (!rawError) return null;

  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography
        variant="caption"
        fontWeight={600}
        color="error.main"
        sx={{ mb: 1, display: "block" }}
      >
        Error
      </Typography>

      {/* Title row — icon + classified title */}
      <Box
        sx={(t) => ({
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          p: 1.25,
          borderRadius: "6px",
          border: "1px solid",
          borderColor: alpha(t.palette.error.main, 0.25),
          bgcolor: alpha(
            t.palette.error.main,
            t.palette.mode === "dark" ? 0.1 : 0.05,
          ),
        })}
      >
        <Box
          sx={(t) => ({
            width: 28,
            height: 28,
            borderRadius: "6px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: alpha(t.palette.error.main, 0.15),
            flexShrink: 0,
          })}
        >
          <Iconify
            icon={classified.icon}
            width={16}
            sx={{ color: "error.main" }}
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="subtitle2"
            fontWeight={600}
            sx={{ fontSize: "12px", lineHeight: 1.4, mb: 0.25 }}
          >
            {classified.title}
          </Typography>
          <Typography
            variant="body2"
            sx={{
              fontSize: "11px",
              fontFamily: "monospace",
              color: "text.secondary",
              wordBreak: "break-word",
              lineHeight: 1.5,
            }}
          >
            {classified.normalized}
          </Typography>
        </Box>
      </Box>

      {/* Actionable hints */}
      {classified.hints?.length > 0 && (
        <Box sx={{ mt: 1.25 }}>
          <Typography
            variant="caption"
            fontWeight={600}
            sx={{
              display: "block",
              fontSize: "10px",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
              color: "text.disabled",
              mb: 0.5,
            }}
          >
            How to fix
          </Typography>
          <Box
            component="ul"
            sx={{
              m: 0,
              pl: 2,
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            {classified.hints.map((hint, i) => (
              <Typography
                // eslint-disable-next-line react/no-array-index-key
                key={i}
                component="li"
                variant="body2"
                sx={{
                  fontSize: "11px",
                  lineHeight: 1.5,
                  color: "text.secondary",
                }}
              >
                {hint}
              </Typography>
            ))}
          </Box>
        </Box>
      )}

      {/* Raw error — collapsed by default, available for support context */}
      <Box sx={{ mt: 1.25 }}>
        <Box
          component="button"
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "none",
            background: "transparent",
            color: "text.disabled",
            fontSize: "10px",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            fontWeight: 600,
            cursor: "pointer",
            p: 0,
            "&:hover": { color: "text.secondary" },
          }}
        >
          <Iconify
            icon={
              showRaw
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={12}
          />
          Raw error
        </Box>
        {showRaw && (
          <Box
            sx={(t) => ({
              mt: 0.5,
              p: 1,
              borderRadius: "4px",
              bgcolor: alpha(t.palette.error.main, 0.06),
              border: "1px solid",
              borderColor: alpha(t.palette.error.main, 0.15),
            })}
          >
            <Typography
              sx={{
                fontSize: "11px",
                fontFamily: "monospace",
                color: "text.secondary",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                lineHeight: 1.5,
              }}
            >
              {rawError}
            </Typography>
          </Box>
        )}
      </Box>
    </Box>
  );
};

ErrorDetails.propTypes = {
  rawError: PropTypes.string,
};

// ── Detail panel content (Formatted / JSON toggle) ──
const DetailPanelContent = ({ row, isDark }) => {
  const [viewMode, setViewMode] = useState("formatted");
  const detail = row.detail || {};
  const json = useMemo(() => JSON.stringify(detail, null, 2), [detail]);
console.log("detail", row);
  return (
    <Box
      sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
    >
      {/* Tab toggle */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          gap: 0.5,
        }}
      >
        {["formatted", "json"].map((m) => (
          <Box
            key={m}
            onClick={() => setViewMode(m)}
            sx={{
              px: 1.5,
              py: 0.375,
              borderRadius: "5px",
              fontSize: "11px",
              cursor: "pointer",
              fontWeight: viewMode === m ? 600 : 400,
              color: viewMode === m ? "text.primary" : "text.disabled",
              backgroundColor:
                viewMode === m
                  ? (t) =>
                      t.palette.mode === "dark"
                        ? "rgba(255,255,255,0.08)"
                        : "action.hover"
                  : "transparent",
            }}
          >
            {m === "formatted" ? "Formatted" : "JSON"}
          </Box>
        ))}
      </Box>

      {/* Scrollable content area. `pb: 6` keeps the last field above
          any floating chat/help widgets pinned to the viewport's
          bottom-right. */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", pb: 6 }}>
        {viewMode === "json" ? (
          <Box sx={{ height: 360 }}>
            <Editor
              key={row.id}
              height="100%"
              language="json"
              value={json}
              theme={isDark ? "vs-dark" : "vs"}
              options={{
                readOnly: true,
                minimap: { enabled: false },
                fontSize: 12,
                fontFamily: "'Fira Code', Menlo, monospace",
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                automaticLayout: true,
                wordWrap: "on",
                folding: true,
                padding: { top: 8 },
                renderLineHighlight: "none",
                domReadOnly: true,
              }}
            />
          </Box>
        ) : (
          <Box sx={{ px: 1.5, py: 1 }}>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0 }}>
              <DetailRow
                label="Score"
                value={
                  row.score != null
                    ? typeof row.score === "number"
                      ? row.score.toFixed(2)
                      : String(row.score)
                    : "—"
                }
                color={
                  typeof row.score === "number"
                    ? row.score >= 0.7
                      ? "success.main"
                      : row.score >= 0.3
                        ? "warning.main"
                        : "error.main"
                    : undefined
                }
              />
              {row.result && (
                <DetailRow
                  label="Result"
                  value={row.result}
                  chip
                  chipColor={
                    row.result === "Passed" || row.result === "Pass"
                      ? "success"
                      : row.result === "Failed" ||
                          row.result === "Fail" ||
                          row.result === "Error"
                        ? "error"
                        : "default"
                  }
                />
              )}
              <DetailRow
                label="Status"
                value={row.status || "—"}
                chip
                chipColor={
                  row.status === "success"
                    ? "success"
                    : row.status === "error"
                      ? "error"
                      : "default"
                }
              />
              {detail.eval_name && (
                <DetailRow label="Eval" value={detail.eval_name} />
              )}
              {detail.model && (
                <DetailRow label="Model" value={detail.model} mono />
              )}
              {detail.span_name && (
                <DetailRow label="Span" value={detail.span_name} mono />
              )}
              {detail.span_id && (
                <DetailRow label="Span ID" value={detail.span_id} mono />
              )}
              {detail.trace_id && (
                <DetailRow label="Trace ID" value={detail.trace_id} mono />
              )}
              {row.created_at && (
                <DetailRow
                  label="Ran at"
                  value={new Date(row.created_at).toLocaleString()}
                />
              )}

              {/* Input Variables — the eval mapping resolved against
                  this span. Each variable's value can be a string,
                  number, dict, or list. DetailRow auto-switches to
                  JsonValueTree for non-primitive values so users can
                  drill into nested keys (e.g. `prompt.messages.0.content`)
                  instead of seeing `[object Object]`. */}
              {detail.input_variables &&
                typeof detail.input_variables === "object" &&
                Object.keys(detail.input_variables).length > 0 && (
                  <>
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{
                        mt: 1.5,
                        mb: 0.5,
                        display: "block",
                        fontSize: "11px",
                        textTransform: "uppercase",
                        letterSpacing: "0.5px",
                        color: "text.disabled",
                      }}
                    >
                      Input Variables
                    </Typography>
                    {Object.entries(detail.input_variables).map(
                      ([varName, varValue]) => (
                        <DetailRow
                          key={varName}
                          label={varName}
                          value={varValue}
                          mono
                        />
                      ),
                    )}
                  </>
                )}

              {/* Error rows get a structured ErrorDetails block (icon
                  + classified title + actionable hints + raw collapsed).
                  Successful rows get the eval's reasoning as a plain
                  Explanation block. We pick whichever raw string is
                  available — `error_message` is set on errors,
                  `reason` is set on both. */}
              {row.status === "error" ? (
                <ErrorDetails rawError={detail.error_message || row.reason} />
              ) : (
                row.reason && (
                  <>
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ mt: 1.5, mb: 0.5 }}
                    >
                      Explanation
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        fontSize: "12px",
                        color: "text.secondary",
                        lineHeight: 1.6,
            
                      }}
                    >
                      {row.reason}
                    </Typography>
                  </>
                )
              )}
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

// ── Main ──
const TaskUsageTab = ({ taskId }) => {
  const settings = useSettingsContext();
  const isDark = settings.themeMode === "dark";

  const [dateOption, setDateOption] = useState("30D");
  const [dateFilter, setDateFilter] = useState(null);
  const [page, setPage] = useState(0);
  // Default to 50 per page — tasks typically have many runs and 25 felt
  // too narrow once users started exploring all-time data.
  const [pageSize, setPageSize] = useState(50);
  const [detailIndex, setDetailIndex] = useState(null);
  const [evalIdFilter, setEvalIdFilter] = useState("all");

  const period = DATE_OPTION_TO_PERIOD[dateOption] || "30d";
  const apiEvalId = evalIdFilter === "all" ? undefined : evalIdFilter;

  const { data: chartData, isLoading: chartLoading } = useTaskUsageChart(
    taskId,
    { period, evalId: apiEvalId },
  );
  const {
    data: logsData,
    isLoading: logsLoading,
    isFetching: logsFetching,
  } = useTaskUsageLogs(taskId, { page, pageSize, period, evalId: apiEvalId });

  const stats = chartData?.stats || {};
  const chart = chartData?.chart || [];
  const evalsList = chartData?.evals || [];
  const logItems = logsData?.items || [];
  const totalLogs = logsData?.total || 0;
  // Backend may have widened the window to "all time" if the requested
  // period excluded every run. Surface that to the user as a hint so
  // they don't think the date filter is broken.
  const periodFallback =
    chartData?.periodUsed === "all" &&
    chartData?.periodRequested &&
    chartData?.periodRequested !== "all";

  // Pick the chart's output type. With the "all evals" filter we default
  // to pass_fail. With a specific eval selected, we use that eval's
  // output_type so the chart switches between pass-rate and avg-score.
  const chartOutputType = useMemo(() => {
    if (evalIdFilter === "all") {
      // If every configured eval is the same type, use it; otherwise default
      // to pass_fail (most common).
      const types = new Set(evalsList.map((e) => e.output_type));
      if (types.size === 1) return [...types][0];
      return "pass_fail";
    }
    const sel = evalsList.find((e) => e.id === evalIdFilter);
    return sel?.output_type || "pass_fail";
  }, [evalIdFilter, evalsList]);

  // Logs are server-side paginated. The eval-usage tab has a client-side
  // search box but it's misleading on a paginated table — users would
  // search "X" expecting all-task results but only see matches on the
  // current page. We omit it here; if a search is needed, it should be
  // wired through to the backend as a query param.
  const visibleLogs = logItems;

  const columns = useColumns();
  const handleRowClick = useCallback(
    (row) => {
      const idx = visibleLogs.findIndex((l) => l.id === row.id);
      setDetailIndex(idx >= 0 ? idx : 0);
    },
    [visibleLogs],
  );

  const detailRow = detailIndex !== null ? visibleLogs[detailIndex] : null;

  // Keyboard shortcuts: j/k for next/prev, Esc to close — same as eval usage
  useEffect(() => {
    if (detailIndex === null) return undefined;
    const handler = (e) => {
      if (e.key === "k") {
        e.preventDefault();
        setDetailIndex((i) => Math.max(0, (i ?? 0) - 1));
      } else if (e.key === "j") {
        e.preventDefault();
        setDetailIndex((i) => Math.min(visibleLogs.length - 1, (i ?? 0) + 1));
      } else if (e.key === "Escape") {
        setDetailIndex(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [detailIndex, visibleLogs.length]);

  return (
    // `position: relative` so the absolutely-positioned detail panel
    // (below) anchors against this container instead of the document.
    <Box
      sx={{
        display: "flex",
        height: "100%",
        minHeight: 0,
        position: "relative",
      }}
    >
      {/* ── Main content ── */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          minWidth: 0,
          p: 2,
          transition: "margin-right 0.25s",
        }}
      >
        {/* Date picker + eval filter + stats */}
        <Box
          sx={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            pb: 1.5,
            gap: 2,
            flexWrap: "wrap",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexShrink: 0,
            }}
          >
            <DateTimeRangePicker
              dateOption={dateOption}
              setDateOption={(opt) => {
                setDateOption(opt);
                setPage(0);
              }}
              setParentDateFilter={setDateFilter}
              dateFilter={dateFilter}
            />
            {/* Eval filter — only show when the task has >1 configured eval */}
            {evalsList.length > 1 && (
              <Select
                value={evalIdFilter}
                onChange={(e) => {
                  setEvalIdFilter(e.target.value);
                  setPage(0);
                  setDetailIndex(null);
                }}
                size="small"
                sx={{
                  fontSize: "12px",
                  height: 32,
                  minWidth: 180,
                }}
              >
                <MenuItem value="all" sx={{ fontSize: "12px" }}>
                  All evaluations
                </MenuItem>
                {evalsList.map((ev) => (
                  <MenuItem key={ev.id} value={ev.id} sx={{ fontSize: "12px" }}>
                    {ev.name}
                  </MenuItem>
                ))}
              </Select>
            )}
          </Box>
          {!chartLoading && (
            <Box
              sx={{
                display: "flex",
                gap: 2,
                alignItems: "center",
                flexShrink: 0,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
                px: 1.5,
                py: 0.5,
              }}
            >
              <StatPill label="Runs" value={stats.runs_period ?? 0} />
              <Box
                sx={{ width: "1px", height: 14, backgroundColor: "divider" }}
              />
              <StatPill
                label="Success"
                value={stats.success_count ?? 0}
                color="success.main"
              />
              <Box
                sx={{ width: "1px", height: 14, backgroundColor: "divider" }}
              />
              <StatPill
                label="Errors"
                value={stats.error_count ?? 0}
                color="error.main"
              />
              <Box
                sx={{ width: "1px", height: 14, backgroundColor: "divider" }}
              />
              <StatPill
                label="Task Completion Rate"
                value={`${stats.pass_rate ?? 0}%`}
                color="info.main"
              />
            </Box>
          )}
        </Box>

        {/* Period fallback hint — backend widened to "all time" because
            the user-selected window had no runs */}
        {periodFallback && (
          <Box
            sx={(t) => ({
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              px: 1.25,
              py: 0.75,
              mb: 1,
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor:
                t.palette.mode === "dark"
                  ? "rgba(255,255,255,0.03)"
                  : "background.neutral",
            })}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              sx={{ color: "info.main", flexShrink: 0 }}
            />
            <Typography
              variant="caption"
              sx={{ fontSize: "11px", color: "text.secondary" }}
            >
              No runs in the selected window — showing all-time data instead.
            </Typography>
          </Box>
        )}

        {/* Chart */}
        <Box
          sx={{
            flexShrink: 0,
            height: 180,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            p: 1.5,
            mb: 1.5,
            backgroundColor: (t) =>
              t.palette.mode === "dark"
                ? "rgba(255,255,255,0.01)"
                : "background.default",
          }}
        >
          {chartLoading ? (
            <Skeleton variant="rounded" width="100%" height="100%" />
          ) : chart.length > 0 ? (
            <UsageChart data={chart} outputType={chartOutputType} />
          ) : (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
              }}
            >
              <Typography variant="caption" color="text.disabled">
                No data for this period
              </Typography>
            </Box>
          )}
        </Box>

        {/* Logs table */}
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              mb: 1,
              flexShrink: 0,
            }}
          >
            <Typography variant="body2" fontWeight={600}>
              Evaluation Runs
              {logsFetching && (
                <Typography
                  component="span"
                  variant="caption"
                  color="text.disabled"
                  sx={{ ml: 1 }}
                >
                  Updating...
                </Typography>
              )}
            </Typography>
            {totalLogs > 0 && (
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ fontSize: "11px" }}
              >
                {totalLogs.toLocaleString()} total run
                {totalLogs !== 1 ? "s" : ""}
              </Typography>
            )}
          </Box>
          {/* `display: flex` is required here so the inner DataTable Box
              (which itself uses `flex: 1`) actually inherits a bounded
              height. Without it the DataGrid renders every row at
              natural height and the table grows past the viewport,
              hiding the pagination footer and breaking internal scroll. */}
          <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
            <DataTable
              columns={columns}
              data={visibleLogs}
              isLoading={logsLoading && !logsData}
              rowCount={totalLogs}
              onRowClick={handleRowClick}
              emptyMessage="No evaluation runs for this period"
            />
          </Box>
          <Box sx={{ flexShrink: 0 }}>
            <DataTablePagination
              page={page}
              pageSize={pageSize}
              total={totalLogs}
              onPageChange={setPage}
              onPageSizeChange={(s) => {
                setPageSize(s);
                setPage(0);
              }}
              pageSizeOptions={[25, 50, 100]}
            />
          </Box>
        </Box>
      </Box>

      {/* ── Side panel (non-modal overlay) ──
          Anchored to the right edge of the parent (which has
          `position: relative`). Positioning is `absolute` rather than
          inline-flex because:
          1. The flex-sibling layout was producing a chain of nested
             `flex: 1` containers that occasionally lost their constraint
             — content past "Span ID" was clipped without scroll.
          2. Absolute positioning gives the panel its own stacking
             context with a high `zIndex`, so floating chat widgets
             floating widgets don't overlap the content.
          3. The table on the left stays full-width when the panel
             opens — no squished columns. */}
      <Slide
        direction="left"
        in={detailIndex !== null}
        mountOnEnter
        unmountOnExit
      >
        <Box
          sx={{
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: 480,
            zIndex: 10,
            borderLeft: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            backgroundColor: "background.paper",
            // Soft shadow on the left edge so the panel visually
            // separates from the table behind it.
            boxShadow: (t) =>
              t.palette.mode === "dark"
                ? "-8px 0 24px rgba(0,0,0,0.4)"
                : "-8px 0 24px rgba(0,0,0,0.08)",
          }}
        >
          {/* Header with prev/next */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              px: 1.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Tooltip title="Previous (k)">
                <span>
                  <IconButton
                    size="small"
                    disabled={detailIndex === 0}
                    onClick={() =>
                      setDetailIndex((i) => Math.max(0, (i ?? 0) - 1))
                    }
                  >
                    <Iconify icon="mingcute:arrow-up-line" width={16} />
                  </IconButton>
                </span>
              </Tooltip>
              <Tooltip title="Next (j)">
                <span>
                  <IconButton
                    size="small"
                    disabled={detailIndex >= visibleLogs.length - 1}
                    onClick={() =>
                      setDetailIndex((i) =>
                        Math.min(visibleLogs.length - 1, (i ?? 0) + 1),
                      )
                    }
                  >
                    <Iconify icon="mingcute:arrow-down-line" width={16} />
                  </IconButton>
                </span>
              </Tooltip>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ ml: 0.5 }}
              >
                {detailIndex !== null
                  ? `${detailIndex + 1} / ${visibleLogs.length}`
                  : ""}
              </Typography>
              <Box sx={{ display: "flex", gap: 0.25, ml: 0.5 }}>
                <Box
                  sx={{
                    px: 0.5,
                    py: 0.125,
                    borderRadius: "3px",
                    fontSize: "9px",
                    fontFamily: "monospace",
                    border: "1px solid",
                    borderColor: "divider",
                    color: "text.disabled",
                    lineHeight: 1.4,
                  }}
                >
                  k
                </Box>
                <Box
                  sx={{
                    px: 0.5,
                    py: 0.125,
                    borderRadius: "3px",
                    fontSize: "9px",
                    fontFamily: "monospace",
                    border: "1px solid",
                    borderColor: "divider",
                    color: "text.disabled",
                    lineHeight: 1.4,
                  }}
                >
                  j
                </Box>
              </Box>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              {detailRow && (
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ fontFamily: "monospace", fontSize: "10px" }}
                >
                  {detailRow.id?.slice(0, 12)}
                </Typography>
              )}
              <IconButton size="small" onClick={() => setDetailIndex(null)}>
                <Iconify icon="mingcute:close-line" width={16} />
              </IconButton>
            </Box>
          </Box>
          {detailRow && <DetailPanelContent row={detailRow} isDark={isDark} />}
        </Box>
      </Slide>
    </Box>
  );
};

TaskUsageTab.propTypes = {
  taskId: PropTypes.string.isRequired,
};

export default TaskUsageTab;
