import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import {
  Badge,
  Box,
  Button,
  MenuItem,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import {
  format,
  startOfToday,
  startOfTomorrow,
  startOfYesterday,
  sub,
} from "date-fns";
import Iconify from "src/components/iconify";
import DisplayPanel from "./DisplayPanel";
import TraceFilterPanel from "./TraceFilterPanel";
import BulkActionsBar from "./BulkActionsBar";
import { useTabStoreShallow } from "./tabStore";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
import { formatDate } from "src/utils/report-utils";

const DATE_OPTIONS = [
  { key: "Today", label: "Today" },
  { key: "Yesterday", label: "Yesterday" },
  { key: "7D", label: "Past 7D" },
  { key: "30D", label: "Past 30D" },
  { key: "3M", label: "Past 3M" },
  { key: "6M", label: "Past 6M" },
  { key: "12M", label: "Past 12M" },
  { key: "Custom", label: "Custom range" },
];

const ObserveToolbar = ({
  // Mode: "traces" (default) | "sessions" | "users"
  mode = "traces",
  // When true, always render inline (skip the #observe-toolbar-slot portal).
  // Used by pages that mount their own toolbar outside the main ObserveTabBar,
  // e.g., the User Detail Page.
  inline = false,
  // Date
  dateLabel,
  dateFilter,
  setDateFilter,
  // Filter
  hasActiveFilter,
  isFilterOpen,
  onFilterToggle,
  filters,
  setFilters,
  filterDefinition,
  defaultFilter,
  onApplyExtraFilters,
  // Filter fields override (for sessions/users)
  filterFields,
  // LLM Tracing tab ("trace" | "spans") — when set, TraceFilterPanel
  // prepends the matching id filter(s) to its property picker.
  tab,
  // Columns
  columns,
  onColumnVisibilityChange,
  setColumns: _setColumns,
  onAutoSize,
  autoSizeAllCols,
  onAddCustomColumn,
  // Row height
  cellHeight,
  setCellHeight,
  // View mode (graph/agentGraph/agentPath)
  viewMode,
  onViewModeChange,
  // Evals
  hasEvalFilter,
  onToggleEvalFilter,
  showEvalToggle,
  // Metrics
  showErrors,
  onToggleErrors,
  showNonAnnotated,
  onToggleNonAnnotated,
  // Group
  groupBy,
  hiddenGroupByOptions,
  onGroupByChange,
  // Grid
  rowCount,
  // Compare
  onCompareToggle,
  isCompareActive,
  // Bulk actions
  selectedCount,
  onClearSelection,
  onBulkAction,
  bulkActions,
  isSimulator,
  allMatching,
  // Add Evals button
  excludeSimulationCalls,
  onToggleSimulationCalls,
  graphFilters,
  // View persistence
  onResetView,
  onSetDefaultView,
  // External filter anchor (compare mode)
  externalFilterAnchor,
  // Compare mode: which graph's filter is being edited
  filterTarget,
  onApplyCompareExtraFilters,
  // Add Evals — opens prefilled task-create draft
  onAddEvals,
}) => {
  const isTraces = mode === "traces";
  const showAddEvals =
    typeof onAddEvals === "function" &&
    (mode === "traces" || mode === "sessions");
  const [displayAnchor, setDisplayAnchor] = useState(null);
  const filterButtonRef = useRef(null);
  const [panelFilters, setPanelFilters] = useState(null); // stores raw panel-format filters
  const [dateAnchor, setDateAnchor] = useState(null);
  const [customDateOpen, setCustomDateOpen] = useState(false);
  const dateButtonRef = useRef(null);

  const handleDateOptionChange = (option) => {
    setDateAnchor(null);
    if (!setDateFilter) return;
    if (option === "Custom") {
      setCustomDateOpen(true);
      return;
    }
    let filter = null;
    switch (option) {
      case "Today":
        filter = [formatDate(startOfToday()), formatDate(startOfTomorrow())];
        break;
      case "Yesterday":
        filter = [formatDate(startOfYesterday()), formatDate(startOfToday())];
        break;
      case "7D":
        filter = [
          formatDate(sub(new Date(), { days: 7 })),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "30D":
        filter = [
          formatDate(sub(new Date(), { days: 30 })),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "3M":
        filter = [
          formatDate(sub(new Date(), { months: 3 })),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "6M":
        filter = [
          formatDate(sub(new Date(), { months: 6 })),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "12M":
        filter = [
          formatDate(sub(new Date(), { months: 12 })),
          formatDate(startOfTomorrow()),
        ];
        break;
      default:
        break;
    }
    if (filter)
      setDateFilter((prev) => ({
        ...prev,
        dateFilter: filter,
        dateOption: option,
      }));
  };

  // Sync extra filters (the single source of truth) into panelFilters
  useEffect(() => {
    if (!graphFilters?.length) {
      setPanelFilters(null);
      return;
    }
    const opReverseMap = {
      equals: "is",
      not_equals: "is_not",
      contains: "contains",
      not_contains: "not_contains",
      starts_with: "starts_with",
    };
    const NUMBER_OP_SET = new Set([
      "equal_to",
      "not_equal_to",
      "greater_than",
      "greater_than_or_equal",
      "less_than",
      "less_than_or_equal",
      "between",
      "not_between",
    ]);
    const RANGE_OPS = new Set(["between", "not_between"]);
    const newPanelFilters = graphFilters.map((gf) => {
      const rawOp = gf.filter_config?.filter_op || "equals";
      const isNumberOp = NUMBER_OP_SET.has(rawOp);
      const isRange = RANGE_OPS.has(rawOp);
      const rawVal = gf.filter_config?.filter_value;
      let value;
      if (isRange && rawVal) {
        value = String(rawVal)
          .split(",")
          .map((v) => v.trim());
      } else if (isNumberOp) {
        value = rawVal != null ? String(rawVal) : "";
      } else {
        value = rawVal
          ? String(rawVal)
              .split(",")
              .map((v) => v.trim())
          : [];
      }
      // Derive fieldCategory from col_type (reverse of colTypeMap)
      const colTypeReverseMap = {
        SPAN_ATTRIBUTE: "attribute",
        SYSTEM_METRIC: "system",
        EVAL_METRIC: "eval",
        ANNOTATION: "annotation",
      };
      const rawColType =
        gf.filter_config?.col_type || gf.col_type || "SYSTEM_METRIC";
      return {
        field: gf.column_id,
        fieldName: gf.display_name,
        fieldCategory: colTypeReverseMap[rawColType] || "system",
        fieldType: isNumberOp
          ? "number"
          : gf.filter_config?.filter_type === "number"
            ? "number"
            : gf.filter_config?.filter_type === "categorical"
              ? "categorical"
              : gf.filter_config?.filter_type === "text" &&
                  rawColType === "ANNOTATION"
                ? "text"
                : "string",
        operator: isNumberOp ? rawOp : opReverseMap[rawOp] || rawOp,
        value,
      };
    });
    setPanelFilters(newPanelFilters);
  }, [graphFilters]);
  const { openCreateModal } = useTabStoreShallow((s) => ({
    openCreateModal: s.openCreateModal,
  }));

  // Shared pill button style — 26px bordered
  const pillSx = {
    textTransform: "none",
    fontWeight: 500,
    fontSize: 13,
    fontFamily: "'IBM Plex Sans', sans-serif",
    height: 26,
    border: "1px solid",
    borderColor: "divider",
    borderRadius: "4px",
    color: "text.primary",
    bgcolor: "background.paper",
    px: 1,
    "&:hover": { bgcolor: "background.neutral", borderColor: "text.disabled" },
  };

  // Find the portal target in the tab bar
  const [portalTarget, setPortalTarget] = useState(null);
  useEffect(() => {
    // Wait for the tab bar to render the slot
    const el = document.getElementById("observe-toolbar-slot");
    if (el) setPortalTarget(el);
    // Retry in case the slot renders after this component
    const timer = setTimeout(() => {
      const el2 = document.getElementById("observe-toolbar-slot");
      if (el2) setPortalTarget(el2);
    }, 100);
    return () => clearTimeout(timer);
  }, []);

  const toolbarContent = (
    <Stack direction="row" alignItems="center" gap={1}>
      {/* Date picker — hidden in compare mode (each graph has its own) */}
      {dateLabel && !isCompareActive && (
        <>
          <Button
            ref={dateButtonRef}
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="mdi:calendar-outline" width={16} />}
            endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
            onClick={(e) => setDateAnchor(e.currentTarget)}
            sx={{ ...pillSx }}
          >
            {dateLabel}
          </Button>
          <Popover
            open={Boolean(dateAnchor)}
            anchorEl={dateAnchor}
            onClose={() => setDateAnchor(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
            transformOrigin={{ vertical: "top", horizontal: "left" }}
            slotProps={{
              paper: { sx: { mt: 0.5, borderRadius: "8px", minWidth: 140 } },
            }}
          >
            {DATE_OPTIONS.map((opt) => (
              <MenuItem
                key={opt.key}
                selected={dateFilter?.dateOption === opt.key}
                onClick={() => handleDateOptionChange(opt.key)}
                sx={{ fontSize: 13, py: 0.75 }}
              >
                {opt.label}
              </MenuItem>
            ))}
          </Popover>
          <CustomDateRangePicker
            open={customDateOpen}
            onClose={() => setCustomDateOpen(false)}
            anchorEl={dateButtonRef.current}
            setDateFilter={(range) => {
              setDateFilter?.((prev) => ({
                ...prev,
                dateFilter: range,
                dateOption: "Custom",
              }));
              setCustomDateOpen(false);
            }}
            setDateOption={() => {}}
          />
        </>
      )}

      {/* Action buttons OR Bulk actions */}
      {selectedCount > 0 ? (
        <BulkActionsBar
          selectedCount={selectedCount}
          onClearSelection={onClearSelection}
          onAction={onBulkAction}
          isSimulator={isSimulator}
          actions={bulkActions}
          allMatching={allMatching}
        />
      ) : (
        <>
          {/* Filter — hidden in compare mode (each graph has its own) */}
          {!isCompareActive && (
            <Button
              ref={filterButtonRef}
              variant="outlined"
              size="small"
              startIcon={
                hasActiveFilter ? (
                  <Badge variant="dot" color="error" overlap="circular">
                    <Iconify icon="mdi:filter-outline" width={16} />
                  </Badge>
                ) : (
                  <Iconify icon="mdi:filter-outline" width={16} />
                )
              }
              onClick={onFilterToggle}
              sx={{
                ...pillSx,
                bgcolor: isFilterOpen ? "action.hover" : "background.paper",
              }}
            >
              Filter
            </Button>
          )}

          {/* Filter Panel (popover) */}
          <TraceFilterPanel
            anchorEl={externalFilterAnchor || filterButtonRef.current}
            open={isFilterOpen}
            onClose={onFilterToggle}
            currentFilters={panelFilters}
            filterFields={filterFields}
            tab={tab}
            isSimulator={isSimulator}
            source={
              mode === "sessions"
                ? "sessions"
                : mode === "users"
                  ? "users"
                  : "traces"
            }
            onApply={(newFilters) => {
              setPanelFilters(newFilters);
              if (!newFilters || newFilters.length === 0) {
                if (filterTarget === "compare" && onApplyCompareExtraFilters) {
                  onApplyCompareExtraFilters([]);
                } else {
                  onApplyExtraFilters?.([]);
                }
                return;
              }
              const opMap = {
                is: "equals",
                is_not: "not_equals",
                contains: "contains",
                not_contains: "not_contains",
                equals: "equals",
                // Number operators — pass through directly
                equal_to: "equal_to",
                not_equal_to: "not_equal_to",
                greater_than: "greater_than",
                greater_than_or_equal: "greater_than_or_equal",
                less_than: "less_than",
                less_than_or_equal: "less_than_or_equal",
                between: "between",
                not_between: "not_between",
              };
              const typeMap = {
                string: "text",
                number: "number",
                boolean: "boolean",
                categorical: "categorical",
                text: "text",
              };
              const colTypeMap = {
                attribute: "SPAN_ATTRIBUTE",
                system: "SYSTEM_METRIC",
                eval: "EVAL_METRIC",
                annotation: "ANNOTATION",
              };
              const apiFilters = newFilters.map((f) => {
                const baseOp = opMap[f.operator] || f.operator;
                // Multi-value picks (enum / choices) come in as arrays. For
                // the `is`/`is_not` (equals/not_equals) ops, promote to
                // `in`/`not_in` so the backend sees a proper IN clause
                // instead of an equality check against a joined string.
                let filterOp = baseOp;
                let filterValue = f.value;
                if (Array.isArray(filterValue)) {
                  if (filterValue.length === 1) {
                    filterValue = filterValue[0];
                  } else if (filterValue.length > 1) {
                    if (baseOp === "equals") filterOp = "in";
                    else if (baseOp === "not_equals") filterOp = "not_in";
                    else filterValue = filterValue.join(",");
                  }
                }
                return {
                  column_id: f.field,
                  ...(f.fieldName && { display_name: f.fieldName }),
                  filter_config: {
                    filter_type: typeMap[f.fieldType] || "text",
                    filter_op: filterOp,
                    filter_value: filterValue,
                    ...(colTypeMap[f.fieldCategory] && {
                      col_type: colTypeMap[f.fieldCategory],
                    }),
                  },
                };
              });
              // Route to correct handler based on which graph's filter was clicked
              if (filterTarget === "compare" && onApplyCompareExtraFilters) {
                onApplyCompareExtraFilters(apiFilters);
              } else {
                onApplyExtraFilters?.(apiFilters);
              }
            }}
          />

          {/* Save view — appears when filters are active (traces only) */}
          {isTraces && hasActiveFilter && (
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="mdi:content-save-outline" width={16} />}
              onClick={(e) => {
                // Find the "+" button in the tab bar and click it to open the save view popover
                const createBtn = document.querySelector(
                  "[data-create-view-btn]",
                );
                if (createBtn) createBtn.click();
                else openCreateModal();
              }}
              sx={{
                ...pillSx,
                borderColor: "primary.main",
                color: "primary.main",
                "&:hover": {
                  bgcolor: "primary.lighter",
                  borderColor: "primary.main",
                },
              }}
            >
              Save view
            </Button>
          )}

          {/* Display */}
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="mdi:tune-vertical" width={16} />}
            onClick={(e) => setDisplayAnchor(e.currentTarget)}
            sx={{
              ...pillSx,
            }}
          >
            Display
          </Button>

          <DisplayPanel
            anchorEl={displayAnchor}
            open={Boolean(displayAnchor)}
            onClose={() => setDisplayAnchor(null)}
            mode={mode}
            viewMode={viewMode}
            onViewModeChange={onViewModeChange}
            columns={columns}
            onColumnVisibilityChange={onColumnVisibilityChange}
            onAutoSize={onAutoSize}
            autoSizeAllCols={autoSizeAllCols}
            onAddCustomColumn={onAddCustomColumn}
            cellHeight={cellHeight}
            setCellHeight={setCellHeight}
            hasEvalFilter={hasEvalFilter}
            onToggleEvalFilter={onToggleEvalFilter}
            showEvalToggle={showEvalToggle}
            showErrors={showErrors}
            onToggleErrors={onToggleErrors}
            showNonAnnotated={showNonAnnotated}
            onToggleNonAnnotated={onToggleNonAnnotated}
            groupBy={groupBy}
            onGroupByChange={onGroupByChange}
            hiddenGroupByOptions={hiddenGroupByOptions}
            onCompareToggle={onCompareToggle}
            isCompareActive={isCompareActive}
            onResetView={onResetView}
            onSetDefaultView={onSetDefaultView}
            isSimulator={isSimulator}
            excludeSimulationCalls={excludeSimulationCalls}
            onToggleSimulationCalls={onToggleSimulationCalls}
          />

          {/* Add Evals — opens task create with project + filters pre-filled */}
          {onAddEvals && (
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="mdi:plus" width={16} />}
              onClick={onAddEvals}
              sx={{
                ...pillSx,
              }}
            >
              Add Evals
            </Button>
          )}
        </>
      )}
    </Stack>
  );

  if (portalTarget && !inline) {
    return createPortal(toolbarContent, portalTarget);
  }
  return toolbarContent;
};

ObserveToolbar.propTypes = {
  mode: PropTypes.oneOf(["traces", "sessions", "users"]),
  inline: PropTypes.bool,
  dateLabel: PropTypes.string,
  dateFilter: PropTypes.object,
  setDateFilter: PropTypes.func,
  hasActiveFilter: PropTypes.bool,
  isFilterOpen: PropTypes.bool,
  onFilterToggle: PropTypes.func,
  filters: PropTypes.array,
  setFilters: PropTypes.func,
  filterDefinition: PropTypes.array,
  defaultFilter: PropTypes.object,
  columns: PropTypes.array,
  onColumnVisibilityChange: PropTypes.func,
  setColumns: PropTypes.func,
  onAutoSize: PropTypes.func,
  autoSizeAllCols: PropTypes.bool,
  onAddCustomColumn: PropTypes.func,
  cellHeight: PropTypes.string,
  setCellHeight: PropTypes.func,
  viewMode: PropTypes.string,
  onViewModeChange: PropTypes.func,
  hasEvalFilter: PropTypes.bool,
  onToggleEvalFilter: PropTypes.func,
  showEvalToggle: PropTypes.bool,
  showErrors: PropTypes.bool,
  onToggleErrors: PropTypes.func,
  showNonAnnotated: PropTypes.bool,
  onToggleNonAnnotated: PropTypes.func,
  groupBy: PropTypes.string,
  hiddenGroupByOptions: PropTypes.arrayOf(PropTypes.string),
  onGroupByChange: PropTypes.func,
  rowCount: PropTypes.number,
  onCompareToggle: PropTypes.func,
  isCompareActive: PropTypes.bool,
  selectedCount: PropTypes.number,
  allMatching: PropTypes.bool,
  onClearSelection: PropTypes.func,
  onBulkAction: PropTypes.func,
  bulkActions: PropTypes.array,
  onAddEvals: PropTypes.func,
  isSimulator: PropTypes.bool,
  excludeSimulationCalls: PropTypes.bool,
  onToggleSimulationCalls: PropTypes.func,
  onApplyExtraFilters: PropTypes.func,
  filterFields: PropTypes.array,
  tab: PropTypes.oneOf(["trace", "spans"]),
  graphFilters: PropTypes.array,
  onResetView: PropTypes.func,
  onSetDefaultView: PropTypes.func,
  externalFilterAnchor: PropTypes.any,
  filterTarget: PropTypes.string,
  onApplyCompareExtraFilters: PropTypes.func,
};

export default React.memo(ObserveToolbar);
