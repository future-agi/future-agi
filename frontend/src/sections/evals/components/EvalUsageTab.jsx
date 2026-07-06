/* eslint-disable react/prop-types */
import {
  Box,
  ButtonBase,
  Chip,
  IconButton,
  Skeleton,
  Slide,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import { useQueryClient } from "@tanstack/react-query";
import { DataTable, DataTablePagination } from "src/components/data-table";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDebounce } from "src/hooks/use-debounce";
import DateTimeRangePicker from "src/sections/projects/DateTimeRangePicker";
import AddEvalsFeedbackDrawer from "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer";

import PartialInputWarningDetails from "src/sections/common/EvalsTasks/PartialInputWarningDetails";

import { useEvalUsageChart, useEvalUsageLogs } from "../hooks/useEvalUsage";
import { isEditableElement } from "src/utils/keyboardUtils";
import { getStorage, setStorage } from "src/hooks/use-local-storage";
import UsageChart from "./UsageChart";
import SvgColor from "src/components/svg-color";
import ColumnDropdown from "src/components/ColumnDropdown/ColumnDropdown";
import {
  COLUMN_CONFIG_URL_PARAM,
  DATE_OPTION_TO_PERIOD,
  DEFAULT_COLUMN_CONFIG,
  ScoreCell,
  StatPill,
  columnConfigStorageKey,
  decodeColumnConfig,
  encodeColumnConfig,
  normalizeRow,
  useColumns,
} from "../Helpers/evalUsageColumns";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

// StatPill, DATE_OPTION_TO_PERIOD, ScoreCell, useColumns now live in
// ../Helpers/evalUsageColumns (imported above). Keeping this comment so
// future merges from dev know the local copies were removed deliberately.


// ── Main ──
const EvalUsageTab = ({
  templateId,
  outputType = "pass_fail",
  evalType = "llm",
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [dateOption, setDateOption] = useState("30D");
  const [dateFilter, setDateFilter] = useState(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [searchQuery, setSearchQuery] = useState("");
  const [detailIndex, setDetailIndex] = useState(null); // index in filteredLogs
  const debouncedSearch = useDebounce(searchQuery.trim(), 400);
  const columnConfigureRef = useRef(null);

  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);

  const period = DATE_OPTION_TO_PERIOD[dateOption] || "30d";

  // Split queries
  const { data: chartData, isLoading: chartLoading } = useEvalUsageChart(
    templateId,
    period,
    dateOption,
    dateFilter,
  );
  const {
    data: logsData,
    isLoading: logsLoading,
    isFetching: logsFetching,
  } = useEvalUsageLogs(templateId, { page, pageSize, period, dateOption, dateFilter });

  const stats = chartData?.stats || {};
  const chart = chartData?.chart || [];
  const totalLogs = logsData?.pagination?.total || 0;

  const logItems = useMemo(
    () => (logsData?.table || []).map(normalizeRow),
    [logsData?.table],
  );

  const baseColumnConfig = useMemo(() => {
    const base = DEFAULT_COLUMN_CONFIG;
    // Discover input_var_* columns from the row data
    const seen = new Set(base.map((c) => c.value));
    const extra = [];
    (logsData?.table || []).forEach((row) => {
      Object.keys(row).forEach((k) => {
        if (k.startsWith("input_var_") && !seen.has(k)) {
          seen.add(k);
          extra.push({
            value: k,
            label: k.replace("input_var_", ""),
            enabled: false,
            is_visible: false,
            order_index: base.length + extra.length,
          });
        }
      });
    });
    return extra.length ? [...base, ...extra] : base;
  }, [logsData?.table]);

  const storageKey = columnConfigStorageKey(templateId);

  const currentColumnConfig = useMemo(() => {
    const fromUrl = searchParams.get(COLUMN_CONFIG_URL_PARAM);
    const decodedUrl = decodeColumnConfig(fromUrl, baseColumnConfig);
    if (decodedUrl) return decodedUrl;
    const decodedStorage = decodeColumnConfig(getStorage(storageKey), baseColumnConfig);
    if (decodedStorage) return decodedStorage;
    return baseColumnConfig;
  }, [searchParams, baseColumnConfig, storageKey]);

  const columnDropdownItems = useMemo(
    () =>
      [...currentColumnConfig]
        .sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0))
        .map((c) => ({
          id: c.value,
          name: c.label,
          isVisible: !!(c.enabled && c.is_visible),
        })),
    [currentColumnConfig],
  );

  const applyColumnConfig = useCallback(
    (nextColumns) => {
      const encoded = encodeColumnConfig(nextColumns);
      setStorage(storageKey, encoded);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set(COLUMN_CONFIG_URL_PARAM, encoded);
          return next;
        },
        { replace: true },
      );
    },
    [storageKey, setSearchParams],
  );

  const handleColumnVisibilityChange = useCallback(
    (columnId) => {
      const next = currentColumnConfig.map((c) => {
        if (c.value !== columnId) return c;
        const nextVisible = !(c.enabled && c.is_visible);
        return { ...c, enabled: nextVisible, is_visible: nextVisible };
      });
      applyColumnConfig(next);
    },
    [currentColumnConfig, applyColumnConfig],
  );

  const handleColumnReorder = useCallback(
    (reordered) => {
      const byValue = new Map(currentColumnConfig.map((c) => [c.value, c]));
      const next = reordered
        .map((item, idx) => {
          const orig = byValue.get(item.id);
          return orig ? { ...orig, order_index: idx } : null;
        })
        .filter(Boolean);
      applyColumnConfig(next);
    },
    [currentColumnConfig, applyColumnConfig],
  );

  const filteredLogs = useMemo(() => {
    if (!debouncedSearch) return logItems;
    const q = debouncedSearch.toLowerCase();
    return logItems.filter(
      (l) =>
        l.id?.toLowerCase?.().includes(q) ||
        l.input?.toLowerCase?.().includes(q) ||
        l.result?.toLowerCase?.().includes(q) ||
        l.reason?.toLowerCase?.().includes(q),
    );
  }, [logItems, debouncedSearch]);

  const columns = useColumns(currentColumnConfig);
  const handleRowClick = useCallback(
    (row) => {
      const idx = filteredLogs.findIndex((l) => l.id === row.id);
      setDetailIndex(idx >= 0 ? idx : 0);
    },
    [filteredLogs],
  );

  const detailRow = detailIndex !== null ? filteredLogs[detailIndex] : null;

  const handleFeedbackSubmitted = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["evals", "usage-logs", templateId],
    });
  }, [queryClient, templateId]);

  // Keyboard shortcuts for prev/next when panel is open
  React.useEffect(() => {
    if (detailIndex === null) return;
    const handler = (e) => {
      if (e.repeat) return;
      if (isEditableElement(e)) return;
      if (e.key === "k") {
        e.preventDefault();
        setDetailIndex((i) => Math.max(0, (i ?? 0) - 1));
      } else if (e.key === "j") {
        e.preventDefault();
        setDetailIndex((i) => Math.min(filteredLogs.length - 1, (i ?? 0) + 1));
      } else if (e.key === "Escape") {
        setDetailIndex(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [detailIndex, filteredLogs.length]);

  return (
    <Box
      sx={{
        display: "flex",
        height: "100%",
        minHeight: 0,
        position: "relative",
        overflow: "hidden",
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
          marginRight: detailIndex !== null ? "420px" : 0,
          transition: "margin-right 0.25s",
        }}
      >
        {/* Date picker + stats — single row */}
        <Box
          sx={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            pb: 1.5,
            gap: 2,
          }}
        >
          <Box sx={{ flexShrink: 0 }}>
            <DateTimeRangePicker
              dateOption={dateOption}
              setDateOption={(opt) => {
                setDateOption(opt);
                setPage(0);
                if (opt !== "Custom") setDateFilter(null);
              }}
              setParentDateFilter={setDateFilter}
              dateFilter={dateFilter}
            />
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
            <UsageChart data={chart} outputType={outputType} />
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
              No data to show for selected period, update filters to view graph/data when no data is available.
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
              Evaluation Logs
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
            <Box sx={{ width: 200, display: "flex", alignItems: "center", gap: 1, }}>
              <CustomTooltip

                show={true}
                title={"View Column"}
                placement="bottom"
                arrow
                size="small"
              >
                <IconButton
                  ref={columnConfigureRef}
                  onClick={() => setOpenColumnConfigure((prev) => !prev)}
                  size="small"
                  sx={{ color: "text.secondary" }}
                >
                  <SvgColor
                    src="/assets/icons/action_buttons/ic_column.svg"
                    sx={{ width: 16, height: 16, color: "text.primary" }}
                  />
                </IconButton>
              </CustomTooltip>
              <FormSearchField
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search..."
                size="small"
                sx={{
                  minWidth: "120px",
                  "& .MuiOutlinedInput-root": { height: "30px" },
                }}

              />
            </Box>
          </Box>
          <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            <DataTable
              columns={columns}
              data={filteredLogs}
              isLoading={logsLoading && !logsData}
              rowCount={totalLogs}
              onRowClick={handleRowClick}
              emptyMessage="No evaluation logs for this period"
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
            />
          </Box>
        </Box>
      </Box>

      {/* ── Side panel (non-modal) ── */}
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
            width: 420,
            borderLeft: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            backgroundColor: "background.paper",
            zIndex: 1,
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
              <Tooltip title="Previous">
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
              <Tooltip title="Next">
                <span>
                  <IconButton
                    size="small"
                    disabled={detailIndex >= filteredLogs.length - 1}
                    onClick={() =>
                      setDetailIndex((i) =>
                        Math.min(filteredLogs.length - 1, (i ?? 0) + 1),
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
                  ? `${detailIndex + 1} / ${filteredLogs.length}`
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
          {/* Content: Formatted / JSON toggle */}
          {detailRow && (
            <DetailPanelContent
              row={detailRow}
              isDark={isDark}
              templateId={templateId}
              evalType={evalType}
              onFeedbackSubmitted={handleFeedbackSubmitted}
            />
          )}
        </Box>
      </Slide>

      <ColumnDropdown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={columnDropdownItems}
        onColumnVisibilityChange={handleColumnVisibilityChange}
        setColumns={handleColumnReorder}
      />
    </Box>
  );
};

// ── Detail panel content with Formatted/JSON tabs + feedback ──
const DetailPanelContent = ({
  row,
  isDark,
  templateId,
  evalType = "llm",
  onFeedbackSubmitted,
}) => {
  const { role } = useAuthContext();
  const canEditEvals = Boolean(
    RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS]?.[role]
  );
  const [viewMode, setViewMode] = useState("formatted");
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  const detail = row.detail || {};
  const warnings = row.warnings || detail.warnings || [];
  const json = useMemo(() => JSON.stringify(detail, null, 2), [detail]);

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
          <ButtonBase
            key={m}
            onClick={() => setViewMode(m)}
            disableRipple
            sx={{
              px: 1.5,
              py: 0.375,
              borderRadius: "5px",
              fontSize: "11px",
              fontWeight: viewMode === m ? 600 : 400,
              color: viewMode === m ? "text.primary" : "text.secondary",
              backgroundColor:
                viewMode === m
                  ? (t) =>
                    t.palette.mode === "dark"
                      ? "rgba(255,255,255,0.08)"
                      : "action.hover"
                  : "transparent",
              "&:hover": {
                backgroundColor: (t) =>
                  t.palette.mode === "dark"
                    ? "rgba(255,255,255,0.04)"
                    : "action.hover",
              },
            }}
          >
            {m === "formatted" ? "Formatted" : "JSON"}
          </ButtonBase>
        ))}
      </Box>

      {/* Scrollable content area */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {/* Data section — switches between formatted and JSON */}
        {viewMode === "json" ? (
          <Box sx={{ height: 300 }}>
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
              {row.composite && (
                <DetailRow label="Type" value="Composite" chip />
              )}
              <DetailRow
                label={row.composite ? "Agg. Score" : "Score"}
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
              {row.result ? (
                <DetailRow
                  label="Result"
                  value={row.result}
                  chip
                  chipColor={
                    row.result === "Passed" || row.result === "Pass"
                      ? "success"
                      : row.result === "Failed" || row.result === "Fail"
                        ? "error"
                        : "default"
                  }
                />
              ) : row.status === "error" ? (
                <DetailRow
                  label="Result"
                  value="Error"
                  chip
                  chipColor="error"
                />
              ) : null}
              {row.composite && row.detail?.aggregation_function && (
                <DetailRow
                  label="Aggregation"
                  value={row.detail.aggregation_function.replace("_", " ")}
                />
              )}
              {row.composite && row.detail?.total_children != null && (
                <DetailRow
                  label="Children"
                  value={`${row.detail.completed_children ?? 0} / ${row.detail.total_children} completed${row.detail.failed_children ? `, ${row.detail.failed_children} failed` : ""}`}
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
              <PartialInputWarningDetails warnings={warnings} />
              <DetailRow
                label="Source"
                value={
                  row.source === "eval_playground"
                    ? "Playground"
                    : row.source === "dataset_evaluation"
                      ? "Dataset"
                      : row.source === "composite_eval"
                        ? "Playground"
                        : row.source === "composite_eval_dataset"
                          ? "Dataset"
                          : row.source === "tracer_composite"
                            ? "Tracer"
                            : row.source || "—"
                }
              />
              {row.version != null && row.version !== "" && (
                <DetailRow
                  label="Version"
                  value={
                    typeof row.version === "number" ||
                      !String(row.version).startsWith("v")
                      ? `v${row.version}`
                      : String(row.version)
                  }
                />
              )}
              {row.created_at && (
                <DetailRow
                  label="Ran at"
                  value={new Date(row.created_at).toLocaleString()}
                />
              )}

              {detail.input_variables &&
                typeof detail.input_variables === "object" &&
                Object.keys(detail.input_variables).length > 0 && (
                  <>
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ mt: 1.5, mb: 0.5 }}
                    >
                      Input Variables
                    </Typography>
                    {Object.entries(detail.input_variables).map(([k, v]) => (
                      <DetailRow
                        key={k}
                        label={k}
                        value={typeof v === "string" ? v : JSON.stringify(v)}
                        mono
                      />
                    ))}
                  </>
                )}

              {(row.detail?.output?.reason || row.reason) && (
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
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {row.detail?.output?.reason || row.reason}
                  </Typography>
                </>
              )}
            </Box>
          </Box>
        )}

        {/* Composite: children breakdown with "Open in new tab" */}
        {row.composite ? (
          <CompositeChildrenSection row={row} />
        ) : (
          <>
            {/* Feedback section — hidden for code evals (deterministic, no few-shot learning) */}
            {evalType !== "code" && (
              <Box
                sx={{
                  px: 1.5,
                  py: 1.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  variant="caption"
                  fontWeight={600}
                  sx={{ mb: 0.5, display: "block" }}
                >
                  Feedback for Auto Learning
                </Typography>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mb: 1.5, display: "block", lineHeight: 1.5 }}
                >
                  Feedback is embedded and used as few-shot examples to improve
                  future evaluations.
                </Typography>

                {row.feedback ? (
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                      p: 1.5,
                      mb: 1.5,
                    }}
                  >
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        mb: 1,
                      }}
                    >
                      <Box
                        sx={{ display: "flex", alignItems: "center", gap: 1 }}
                      >
                        <Iconify
                          icon={
                            row.feedback.value === "passed"
                              ? "mingcute:thumb-up-2-fill"
                              : "mingcute:thumb-down-2-fill"
                          }
                          width={16}
                          sx={{
                            color:
                              row.feedback.value === "passed"
                                ? "success.main"
                                : "error.main",
                          }}
                        />
                        <Chip
                          label={
                            row.feedback.value === "passed"
                              ? "Correct"
                              : row.feedback.value === "failed"
                                ? "Incorrect"
                                : row.feedback.value
                          }
                          size="small"
                          color={
                            row.feedback.value === "passed"
                              ? "success"
                              : "error"
                          }
                          variant="outlined"
                          sx={{ fontSize: "11px", height: 20 }}
                        />
                      </Box>
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          gap: 0.5,
                        }}
                      >
                        {row.feedback.user && (
                          <Typography
                            variant="caption"
                            color="text.disabled"
                            sx={{ fontSize: "10px" }}
                          >
                            {row.feedback.user}
                          </Typography>
                        )}
                        <IconButton
                          size="small"
                          disabled={!canEditEvals}
                          onClick={() => setFeedbackOpen(true)}
                        >
                          <Iconify
                            icon="mingcute:edit-line"
                            width={14}
                            sx={{ color: "text.secondary" }}
                          />
                        </IconButton>
                      </Box>
                    </Box>
                    {row.feedback.explanation && (
                      <Typography
                        variant="body2"
                        sx={{
                          fontSize: "12px",
                          color: "text.secondary",
                          lineHeight: 1.5,
                        }}
                      >
                        {row.feedback.explanation}
                      </Typography>
                    )}
                    {row.feedback.action_type && (
                      <Chip
                        label={
                          row.feedback.action_type === "retune"
                            ? "Re-tune"
                            : row.feedback.action_type === "recalculate"
                              ? "Re-calculate"
                              : row.feedback.action_type
                        }
                        size="small"
                        variant="outlined"
                        sx={{ fontSize: "10px", height: 18, mt: 0.75 }}
                      />
                    )}
                  </Box>
                ) : null}

                <Box
                  component="button"
                  disabled={!canEditEvals}
                  onClick={() => setFeedbackOpen(true)}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    px: 2,
                    py: 0.75,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    backgroundColor: "transparent",
                    color: "text.primary",
                    cursor: canEditEvals ? "pointer" : "not-allowed",
                    opacity: canEditEvals ? 1 : 0.5,
                    fontSize: "12px",
                    fontWeight: 500,
                    width: "100%",
                    "&:hover": canEditEvals
                      ? {
                          borderColor: "primary.main",
                          backgroundColor: (t) =>
                            t.palette.mode === "dark"
                              ? "rgba(124,77,255,0.06)"
                              : "rgba(124,77,255,0.04)",
                        }
                      : {},
                  }}
                >
                  <Iconify
                    icon={
                      row.feedback
                        ? "mingcute:edit-line"
                        : "mingcute:message-3-line"
                    }
                    width={14}
                    sx={{ color: "primary.main" }}
                  />
                  {row.feedback ? "Edit Feedback" : "Add Feedback"}
                </Box>
              </Box>
            )}

            {/* Feedback drawer — hidden for code evals */}
            {evalType !== "code" && (
              <AddEvalsFeedbackDrawer
                open={feedbackOpen}
                onClose={(submitted) => {
                  setFeedbackOpen(false);
                  if (submitted) onFeedbackSubmitted?.();
                }}
                selectedAddFeedback={{ id: row.id }}
                output={{
                  reason: row.detail?.output?.reason || row.reason || "",
                }}
                evalsId={templateId}
                existingFeedback={row.feedback || null}
              />
            )}
          </>
        )}
      </Box>
    </Box>
  );
};

// ── Composite children section in detail panel ──
const CompositeChildrenSection = ({ row }) => {
  const children = row.detail?.children || [];
  const aggFunction = row.detail?.aggregation_function || "weighted_avg";

  return (
    <Box
      sx={{
        px: 1.5,
        py: 1.5,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 1,
        }}
      >
        <Typography variant="caption" fontWeight={600}>
          Child Evaluators ({children.length})
        </Typography>
        <Chip
          label={aggFunction.replace("_", " ")}
          size="small"
          variant="outlined"
          sx={{ fontSize: "10px", height: 18, textTransform: "capitalize" }}
        />
      </Box>

      {children.length === 0 ? (
        <Typography variant="caption" color="text.disabled">
          No child results available
        </Typography>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
          {children.map((child) => {
            const isFailed = child.status === "failed";
            const scoreDisplay =
              child.score != null ? child.score.toFixed(2) : "—";
            return (
              <Box
                key={child.child_id}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  p: 1.25,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    mb: 0.5,
                  }}
                >
                  <Typography
                    variant="body2"
                    fontWeight={600}
                    sx={{ fontSize: "12px" }}
                    noWrap
                  >
                    {child.child_name}
                  </Typography>
                  {isFailed ? (
                    <Chip
                      label="Failed"
                      size="small"
                      color="error"
                      variant="outlined"
                      sx={{ fontSize: "10px", height: 18 }}
                    />
                  ) : (
                    <ScoreCell value={child.score} />
                  )}
                </Box>

                {child.reason && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      fontSize: "11px",
                      lineHeight: 1.4,
                      mb: 0.75,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {child.reason}
                  </Typography>
                )}

                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <Typography
                    variant="caption"
                    color="text.disabled"
                    sx={{ fontSize: "10px" }}
                  >
                    weight: {child.weight ?? 1}
                    {!isFailed && ` · score: ${scoreDisplay}`}
                  </Typography>
                  <Tooltip title="Open eval usage in new tab">
                    <IconButton
                      size="small"
                      onClick={() =>
                        window.open(
                          `/dashboard/evaluations/${child.child_id}?tab=usage`,
                          "_blank",
                        )
                      }
                      sx={{ p: 0.25 }}
                    >
                      <Iconify
                        icon="mingcute:external-link-line"
                        width={14}
                        sx={{ color: "primary.main" }}
                      />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>
            );
          })}
        </Box>
      )}
    </Box>
  );
};


const DetailRow = ({ label, value, color, chip, chipColor, mono }) => (
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

EvalUsageTab.propTypes = {
  templateId: PropTypes.string.isRequired,
};

export default EvalUsageTab;
