import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import { Badge, Button, MenuItem, Popover, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import DisplayPanel from "./DisplayPanel";
import TraceFilterPanel from "./TraceFilterPanel";
import BulkActionsBar from "./BulkActionsBar";
import EvaluateMenu from "./InstantEval/EvaluateMenu";
import { pillSx } from "./toolbarStyles";
import { useTabStoreShallow } from "./tabStore";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
import { observePresetDateFilter } from "../timeWindowPresets";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import {
  apiFiltersToPanelRows,
  filterPanelSettings,
} from "../observeFilterConfig";

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

// Views whose rows Instant eval can ask about (traces mode covers the Spans
// tab too). Insights runs on all of them but spans.
const ASK_MODES = new Set(["traces", "sessions", "users"]);

const ObserveToolbar = ({
  // Mode: "traces" (default) | "sessions" | "users"
  mode = "traces",
  // Explicit project scope for mounts whose route has no `observeId` (for
  // example a cross-project Users detail page after its Project filter is
  // selected). TraceFilterPanel otherwise cannot load either its property
  // catalog or retained attribute-key catalog on those mounts.
  projectId,
  // Cross-project user detail has no route project. Its unified catalog is
  // authorized by the active workspace until a Project filter narrows it.
  allowWorkspaceScope = false,
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
  canSaveView,
  onSaveView,
  isFilterOpen,
  onFilterToggle,
  onApplyExtraFilters,
  // Called when the panel's Clear all (or empty Apply) resets extraFilters
  // — owns the localStorage cleanup parent state can't reach from here.
  onClearExtraFilters,
  onClearCompareExtraFilters,
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
  agentGraphEnabled = true,
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
  selectedCountIsLowerBound,
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
  // Evaluate menu: Add evaluators opens a prefilled task-create draft;
  // Ask a question opens the Ask page on this view's date and filters; Find
  // patterns hands the view to a new Insights report.
  onAddEvals,
  onInstantEval,
  onInsights,
  // Spans view — swaps "Trace Name" filter label to "Span Name"
  isSpansView = false,
}) => {
  // Asking needs a project (the views only pass handlers when they have one)
  // and isn't offered in compare mode, nor on a simulator (voice) project:
  // Ask and Find patterns read the trace list, not the calls on screen (and
  // not its "Show simulated calls" toggle). The tab bar hides Insights there
  // the same way.
  const canAsk = ASK_MODES.has(mode) && !isCompareActive && !isSimulator;
  const askUnit = mode === "traces" && isSpansView ? "spans" : mode;
  const [displayAnchor, setDisplayAnchor] = useState(null);
  const filterButtonRef = useRef(null);
  const [filterButtonEl, setFilterButtonEl] = useState(null);
  // Callers without graphFilters keep the last applied rows locally.
  const [appliedPanelFilters, setAppliedPanelFilters] = useState(null);
  const [dateAnchor, setDateAnchor] = useState(null);
  const [customDateOpen, setCustomDateOpen] = useState(false);
  const dateButtonRef = useRef(null);
  // The panel's catalog settings for this mode (see filterPanelSettings).
  const {
    effectiveFilterTab,
    propertyNamespace,
    source: filterValueSource,
    attributeSource,
  } = filterPanelSettings({ mode, tab, isSimulator });
  const setFilterButtonNode = useCallback((node) => {
    filterButtonRef.current = node;
    setFilterButtonEl(node);
  }, []);

  const handleDateOptionChange = (option) => {
    setDateAnchor(null);
    if (!setDateFilter) return;
    if (option === "Custom") {
      setCustomDateOpen(true);
      return;
    }
    // One shared window per preset: hour-floored start, next-midnight end,
    // identical to the default load (see observePresetDateFilter).
    const filter = observePresetDateFilter(option);
    if (filter)
      setDateFilter((prev) => ({
        ...prev,
        dateFilter: filter,
        dateOption: option,
      }));
  };

  // Derived during render, not synced in an effect: TraceFilterPanel seeds its
  // rows on the render it opens, which is also the render a graph Filter
  // button switches filterTarget, so an effect would hand it the other
  // graph's filters.
  const graphPanelFilters = useMemo(
    () => apiFiltersToPanelRows(graphFilters),
    [graphFilters],
  );
  const panelFilters =
    graphFilters === undefined ? appliedPanelFilters : graphPanelFilters;
  const { openCreateModal } = useTabStoreShallow((s) => ({
    openCreateModal: s.openCreateModal,
  }));

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
            value={dateFilter?.dateFilter}
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
          selectedCountIsLowerBound={selectedCountIsLowerBound}
        />
      ) : (
        <>
          {/* Filter — hidden in compare mode (each graph has its own) */}
          {!isCompareActive && (
            <Button
              ref={setFilterButtonNode}
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
            anchorEl={externalFilterAnchor || filterButtonEl}
            open={
              isFilterOpen && Boolean(externalFilterAnchor || filterButtonEl)
            }
            onClose={onFilterToggle}
            currentFilters={panelFilters}
            filterFields={filterFields}
            tab={effectiveFilterTab}
            isSimulator={isSimulator}
            isSpansView={isSpansView}
            source={filterValueSource}
            propertyNamespace={propertyNamespace}
            attributeSource={attributeSource}
            projectId={projectId}
            allowWorkspaceScope={allowWorkspaceScope}
            onApply={(newFilters) => {
              setAppliedPanelFilters(newFilters);
              if (!newFilters || newFilters.length === 0) {
                if (filterTarget === "compare") {
                  if (onClearCompareExtraFilters) {
                    onClearCompareExtraFilters();
                  } else {
                    onApplyCompareExtraFilters?.([]);
                  }
                } else if (onClearExtraFilters) {
                  onClearExtraFilters();
                } else {
                  onApplyExtraFilters?.([]);
                }
                return;
              }
              const apiFilters = newFilters.map(buildApiFilterFromPanelRow);
              // Route to correct handler based on which graph's filter was clicked
              if (filterTarget === "compare" && onApplyCompareExtraFilters) {
                onApplyCompareExtraFilters(apiFilters);
              } else {
                onApplyExtraFilters?.(apiFilters);
              }
            }}
          />

          {/* Save view — updates the currently-active saved view in place
              when its state has diverged from the saved baseline. The "+"
              button in the tab bar handles save-as-new. */}
          {canSaveView && (
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="mdi:content-save-outline" width={16} />}
              onClick={() => {
                if (typeof onSaveView === "function") {
                  onSaveView();
                  return;
                }
                // Fallback: open create-new popover via the "+" button if no
                // explicit save handler was wired (e.g. an older mount path).
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
                  bgcolor: "action.hover",
                  borderColor: "primary.main",
                  color: "primary.main",
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
            agentGraphEnabled={agentGraphEnabled}
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

          {/* Evaluate: ask Jev a question about a sample or every row in
              view, find patterns with Insights, or run saved evaluators. */}
          <EvaluateMenu
            unit={askUnit}
            onAsk={canAsk ? onInstantEval : undefined}
            onFindPatterns={canAsk && !isSpansView ? onInsights : undefined}
            onAddEvaluators={onAddEvals}
          />
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
  projectId: PropTypes.string,
  allowWorkspaceScope: PropTypes.bool,
  inline: PropTypes.bool,
  dateLabel: PropTypes.string,
  dateFilter: PropTypes.object,
  setDateFilter: PropTypes.func,
  hasActiveFilter: PropTypes.bool,
  canSaveView: PropTypes.bool,
  onSaveView: PropTypes.func,
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
  agentGraphEnabled: PropTypes.bool,
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
  selectedCountIsLowerBound: PropTypes.bool,
  allMatching: PropTypes.bool,
  onClearSelection: PropTypes.func,
  onBulkAction: PropTypes.func,
  bulkActions: PropTypes.array,
  onAddEvals: PropTypes.func,
  onInstantEval: PropTypes.func,
  onInsights: PropTypes.func,
  isSimulator: PropTypes.bool,
  excludeSimulationCalls: PropTypes.bool,
  onToggleSimulationCalls: PropTypes.func,
  onApplyExtraFilters: PropTypes.func,
  onClearExtraFilters: PropTypes.func,
  onClearCompareExtraFilters: PropTypes.func,
  filterFields: PropTypes.array,
  tab: PropTypes.oneOf(["trace", "spans", "voiceCalls"]),
  graphFilters: PropTypes.array,
  onResetView: PropTypes.func,
  onSetDefaultView: PropTypes.func,
  externalFilterAnchor: PropTypes.any,
  filterTarget: PropTypes.string,
  onApplyCompareExtraFilters: PropTypes.func,
  isSpansView: PropTypes.bool,
};

export default React.memo(ObserveToolbar);
