import {
  Badge,
  Box,
  Button,
  Chip,
  Divider,
  MenuItem,
  Popover,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, {
  lazy,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import FilterErrorBoundary from "src/components/ComplexFilter/FilterErrorBoundary";
import {
  getRandomId,
  getUniqueColorPalette,
  objectCamelToSnake,
} from "src/utils/utils";

/**
 * Converts graph selections to filter format compatible with the backend API.
 * @param {Array} selectedGraphEvals - Array of selected eval objects
 * @param {Object} selectedGraphAttributes - Object mapping eval id to selected value
 * @param {Array} columns - Column config with reverse output info
 * @returns {Array} Array of filters in the same format as drawer filters
 */
const convertGraphSelectionsToFilters = (
  selectedGraphEvals,
  selectedGraphAttributes,
  columns = [],
) => {
  if (
    !selectedGraphEvals ||
    selectedGraphEvals.length === 0 ||
    !selectedGraphAttributes
  ) {
    return [];
  }

  const filters = [];

  selectedGraphEvals.forEach((evalItem) => {
    if (!evalItem || !evalItem.id) return;

    const selectedValue = selectedGraphAttributes[evalItem.id];
    // Skip if no value is selected (null, undefined)
    if (selectedValue === null || selectedValue === undefined) return;

    // Handle number filters with custom operators
    if (selectedValue?._numberFilter) {
      const { operator, value, value2 } = selectedValue;
      // Skip if no value entered yet
      if (!value && value !== 0) return;

      // For between operators, require both values
      const isBetweenOp = ["between", "not_in_between"].includes(operator);
      if (isBetweenOp && !value2 && value2 !== 0) return;

      // Convert to percentage values (divide by 100) for backend
      const filterValue = isBetweenOp
        ? [parseFloat(value) / 100, parseFloat(value2) / 100]
        : parseFloat(value) / 100;

      filters.push({
        columnId: String(evalItem.id),
        filterConfig: {
          filterType: "number",
          filterOp: operator,
          filterValue: filterValue,
        },
      });
      return;
    }

    const columnConfig = columns.find(
      (col) => col.id === String(evalItem.id) || col.id === evalItem.id,
    );
    const isReversed = columnConfig?.reverseOutput === true;

    let filterValue;
    let filterType = "number";

    const evalOutputType = evalItem.output_type ?? evalItem.outputType;
    // Handle Pass/Fail (boolean) type
    if (evalOutputType === "Pass/Fail" || typeof selectedValue === "boolean") {
      // For Pass/Fail: Passed = 0, Failed = 100 (reversed for reversed evals)
      if (isReversed) {
        filterValue = selectedValue === true ? 0 : 100;
      } else {
        filterValue = selectedValue === true ? 100 : 0;
      }
    } else if (evalOutputType === "score") {
      // For score type, use the value directly
      filterValue = selectedValue;
    } else if (evalOutputType === "choices") {
      // For choices type, use the selected choice as an text
      filterValue = [selectedValue];
      filterType = "array";
    } else {
      // Default: use value as-is
      filterValue = selectedValue;
    }

    filters.push({
      columnId: String(evalItem.id),
      filterConfig: {
        filterType: filterType,
        filterOp: "equals",
        filterValue: filterValue,
      },
    });
  });

  return filters;
};
import { ShowComponent } from "src/components/show";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { formatDate } from "src/utils/report-utils";
import {
  endOfToday,
  startOfToday,
  startOfTomorrow,
  startOfYesterday,
  sub,
} from "date-fns";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { Helmet } from "react-helmet-async";
import { getFilterExtraProperties } from "src/utils/prototypeObserveUtils";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import { useParams, useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";

import { PROJECT_SOURCE } from "src/utils/constants";
import { useLLMTracingFilters } from "./useLLMTracingFilters";
import {
  generateObserveTraceFilterDefinition,
  generateSpanObserveFilterDefinition,
  FILTER_FOR_ERRORS,
  FILTER_FOR_NON_ANNOTATED,
  FILTER_FOR_HAS_EVAL,
} from "./common";
import TracingControls from "./TracingControls";
import ObserveToolbar from "./ObserveToolbar";
import { buildAddEvalsDraft } from "./buildAddEvalsDraft";
import SelectAllBanner from "./SelectAllBanner";
import useProjectFilterField from "../UsersView/useProjectFilterField";
import FilterChips from "./FilterChips";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import {
  getPickerOptionLabel,
  getPickerOptionSecondaryLabel,
  getPickerOptionValue,
} from "./filterValuePickerUtils";
import CustomColumnDialog from "./CustomColumnDialog";
import SvgColor from "src/components/svg-color";
import { ObserveIconButton } from "../SharedComponents";
import { useGetProjectDetails } from "src/api/project/project-detail";
import logger from "src/utils/logger";
import { useTestDetailSideDrawerStoreShallow } from "src/sections/test-detail/states";
import TotalRowsStatusBar from "src/sections/develop-detail/Common/TotalRowsStatusBar";
import CustomTooltip from "src/components/tooltip";
import {
  resetSpanGridStore,
  resetTraceGridStore,
  useLLMTracingStoreShallow,
  useSpanGridStoreShallow,
  useTraceGridStoreShallow,
} from "./states";
import { CircularProgress } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { NULL_OPERATORS } from "../../../components/ComplexFilter/common";
// import ReplayTraces from "./ReplayTraces";
import {
  useReplaySessionsStoreShallow,
  useSessionsGridStore,
} from "../SessionsView/ReplaySessions/store";
import { REPLAY_MODULES } from "../SessionsView/ReplaySessions/configurations";
import { REPLAY_TYPES } from "../SessionsView/ReplaySessions/constants";
import { filtersContentEqual } from "../saved-view-utils";
import { useCreateReplaySessions } from "src/api/project/replay-sessions";
import { enqueueSnackbar } from "notistack";
import {
  useUpdateSavedView,
  useCreateSavedView,
  useUpdateWorkspaceSavedView,
} from "src/api/project/saved-views";

const USER_DETAIL_TAB_TYPE = "user_detail";

// Eagerly load the trace grid (always visible)
import TraceGrid from "./TraceGrid";
import SpanGrid from "./SpanGrid";
import { useAgentGraph } from "src/api/project/agent-graph";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";

// Lazy load graph components — only loaded when viewMode changes
const PrimaryGraph = lazy(() => import("./GraphSection/PrimaryGraph"));
const AgentGraph = lazy(() => import("./GraphSection/AgentGraph"));
const AgentPath = lazy(() => import("./GraphSection/AgentPath"));

// Lazy load conditionally rendered components (modals, drawers)
const CallLogsGrid = lazy(
  () => import("src/sections/agents/CallLogs/CallLogsGrid"),
);
const LLMFiltersDrawer = lazy(() => import("./LLMFiltersDrawer"));
const AddDataset = lazy(
  () => import("src/components/traceDetailDrawer/addToDataset/add-dataset"),
);
const AddTagsPopover = lazy(
  () => import("src/components/traceDetail/AddTagsPopover"),
);
const AddToQueueDialog = lazy(
  () =>
    import("src/sections/annotations/queues/components/add-to-queue-dialog"),
);
const AnnotateDrawer = lazy(
  () => import("src/components/traceDetailDrawer/AnnotateDrawer"),
);
const ColumnConfigureDropDown = lazy(
  () =>
    import(
      "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown"
    ),
);

// Loading fallback component
const ComponentLoader = () => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      minHeight: 200,
    }}
  >
    <CircularProgress size={24} />
  </Box>
);

const defaultFilterBase = {
  columnId: "",
  filterConfig: {
    filterType: "",
    filterOp: "",
    filterValue: "",
  },
};
const getDefaultDateRange = () => {
  const getDateArray = () => {
    return [
      formatDate(
        sub(new Date(), {
          days: 7,
        }),
      ),
      formatDate(endOfToday()),
    ];
  };

  return {
    dateFilter: getDateArray(),
    dateOption: "7D",
  };
};

const getDefaultFilter = () => {
  return [{ ...defaultFilterBase, id: getRandomId() }];
};

const COMPARE_DATE_OPTIONS = [
  { key: "Today", label: "Today" },
  { key: "Yesterday", label: "Yesterday" },
  { key: "7D", label: "Past 7D" },
  { key: "30D", label: "Past 30D" },
  { key: "3M", label: "Past 3M" },
  { key: "6M", label: "Past 6M" },
  { key: "12M", label: "Past 12M" },
  { key: "Custom", label: "Custom range" },
];

const comparePillSx = {
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

// Header row for agent graph/path in compare mode: [A/B badge] [label] [date pill] [filter pill] + inline chips
const CompareGraphHeader = ({
  compareType,
  dateFilter,
  setDateFilter,
  onFilterToggle,
  hasActiveFilter,
  extraFilters,
  onRemoveFilter,
  onClearFilters,
  fieldLabelMap,
}) => {
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

  const paletteIndex = compareType === "primary" ? 1 : 3;
  const label = compareType === "primary" ? "Primary" : "Compare";

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
      <Box
        sx={() => {
          const { tagBackground: bg, tagForeground: text } =
            getUniqueColorPalette(paletteIndex);
          return {
            width: 24,
            height: 25,
            borderRadius: 0.5,
            backgroundColor: bg,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 12,
            fontWeight: 600,
            color: text,
          };
        }}
      >
        {compareType === "primary" ? "A" : "B"}
      </Box>
      <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{label}</Typography>
      <Button
        ref={dateButtonRef}
        variant="outlined"
        size="small"
        startIcon={<Iconify icon="mdi:calendar-outline" width={16} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onClick={(e) => setDateAnchor(e.currentTarget)}
        sx={comparePillSx}
      >
        {dateFilter?.dateOption || "Past 7D"}
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
        {COMPARE_DATE_OPTIONS.map((opt) => (
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
      {onFilterToggle && (
        <Button
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
          onClick={(e) => onFilterToggle(e)}
          sx={comparePillSx}
        >
          Filter
        </Button>
      )}
      {/* Inline filter chips */}
      {extraFilters?.length > 0 && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, ml: 1 }}>
          {extraFilters.map((f, idx) => {
            const field = f?.column_id;
            const op = f?.filter_config?.filter_op || "";
            const val = f?.filter_config?.filter_value;
            const valueMap = fieldLabelMap?.[field];
            const resolveValue = (v) => {
              const key = String(v ?? "");
              return valueMap?.[key] ?? key;
            };
            const opLabel =
              {
                equals: "is",
                not_equals: "is not",
                is: "is",
                is_not: "is not",
                in: "is",
                not_in: "is not",
                contains: "contains",
                not_contains: "not contains",
                starts_with: "starts with",
                ends_with: "ends with",
                equal_to: "equals",
                not_equal_to: "not equal",
                greater_than: "greater than",
                greater_than_or_equal: "greater than or equals",
                less_than: "less than",
                less_than_or_equal: "less than or equals",
                between: "between",
                not_between: "not between",
              }[op] || op;
            const valueStr = Array.isArray(val)
              ? val.map(resolveValue).join(", ")
              : resolveValue(val);
            if (!field) return null;
            return (
              <Chip
                key={idx}
                size="small"
                onDelete={() => onRemoveFilter(idx)}
                label={
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
                      {(() => {
                        if (f.display_name) return f.display_name;
                        // `_.startCase` on a UUID mangles it into spaced
                        // chunks; fall back to a short-id label instead
                        // so chips stay distinguishable.
                        const UUID_RE =
                          /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
                        return UUID_RE.test(field)
                          ? `Column ${String(field).slice(0, 8)}`
                          : _.startCase(field);
                      })()}
                    </Typography>
                    <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
                      {opLabel}
                    </Typography>
                    <Typography
                      sx={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: "text.primary",
                      }}
                    >
                      {valueStr}
                    </Typography>
                  </Box>
                }
                sx={{
                  height: 24,
                  bgcolor: "rgba(0,0,0,0.04)",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "6px",
                  "& .MuiChip-label": { px: 0.5 },
                  "& .MuiChip-deleteIcon": {
                    fontSize: 12,
                    color: "text.disabled",
                  },
                }}
              />
            );
          })}
          <Button
            size="small"
            onClick={onClearFilters}
            sx={{
              textTransform: "none",
              fontSize: 11,
              color: "text.disabled",
              minWidth: "auto",
              p: 0,
            }}
          >
            Clear
          </Button>
        </Box>
      )}
    </Box>
  );
};

CompareGraphHeader.propTypes = {
  compareType: PropTypes.oneOf(["primary", "compare"]).isRequired,
  dateFilter: PropTypes.object,
  setDateFilter: PropTypes.func,
  onFilterToggle: PropTypes.func,
  hasActiveFilter: PropTypes.bool,
  extraFilters: PropTypes.array,
  onRemoveFilter: PropTypes.func,
  onClearFilters: PropTypes.func,
  fieldLabelMap: PropTypes.object,
};

const DEFAULT_DISPLAY_CONFIG = {
  viewMode: "graph",
  cellHeight: "Short",
  showErrors: false,
  showNonAnnotated: false,
  showCompare: false,
  hasEvalFilter: false,
  customColumns: [],
};

const LLMTracingView = ({ mode = "project", userIdForUserMode = null }) => {
  const isUserMode = mode === "user";
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const [selectedGraph, setSelectedGraph] = useUrlState(
    "selectedGraph",
    "primary",
  );
  const [hasEvalFilter, setHasEvalFilter] = useUrlState("hasEvalFilter", false);
  const [selectedTab, setSelectedTab] = useUrlState("selectedTab", "trace");
  const [autoSizeAllCols, setAutoSizeAllCols] = useState(false);
  const [cellHeight, setCellHeight] = useUrlState("cellHeight", "Short");
  const [showErrors, setShowErrors] = useUrlState("showErrors", false);
  const [showNonAnnotated, setShowNonAnnotated] = useUrlState(
    "showNonAnnotated",
    false,
  );
  const [showCompare, setShowCompare] = useUrlState("showCompare", false);
  const [excludeSimulationCalls, setExcludeSimulationCalls] = useUrlState(
    "remove_simulation_calls",
  );
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const [columnConfigureAnchor, setColumnConfigureAnchor] = useState(null);
  const [openAddDataset, setOpenAddDataset] = useState(false);
  const [tagsAnchorEl, setTagsAnchorEl] = useState(null);
  const [tagsBulkItems, setTagsBulkItems] = useState([]);
  const [tagsFetching, setTagsFetching] = useState(false);
  const [queueAnchorEl, setQueueAnchorEl] = useState(null);
  // Filter-mode selection opt-in — one flag per grid. When
  // true, the Actions → Add-to-queue flow POSTs `{selection: {mode:
  // "filter", source_type, ...}}` to the backend (see bulk-select
  // revamp Phases 2 + 4) instead of an enumerated item list. The
  // matching banner is shown only while the grid's `selectAll` is true
  // and the corresponding flag is false.
  const [filterSelectionMode, setFilterSelectionMode] = useState(false); // trace tab
  const [spanFilterSelectionMode, setSpanFilterSelectionMode] = useState(false); // spans tab
  // Simulator CallLogsGrid has its own client-side selection (paginated,
  // no ag-grid server-side inverted-select-all). Banner visibility keys
  // off `simCallMeta.isAllOnPageSelected && totalCount > pageLimit`.
  const [simCallFilterSelectionMode, setSimCallFilterSelectionMode] =
    useState(false);
  const [simCallMeta, setSimCallMeta] = useState({
    isAllOnPageSelected: false,
    currentPageSize: 0,
    totalPages: 1,
    pageLimit: 25,
  });
  const [openAnnotateDrawer, setOpenAnnotateDrawer] = useState(false);
  const { mutate: addAnnotationValues } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.addAnnotationValuesForSpan(), data),
    onSuccess: () => {
      enqueueSnackbar("Annotation saved", { variant: "success" });
      setOpenAnnotateDrawer(false);
    },
    onError: () => {
      enqueueSnackbar("Failed to save annotation", { variant: "error" });
    },
  });
  const [openCustomColumn, setOpenCustomColumn] = useState(false);
  const [extraFilters, setExtraFiltersRaw] = useState([]);
  const [compareExtraFilters, setCompareExtraFiltersRaw] = useState([]);
  const [filterChipsSaved, setFilterChipsSaved] = useState(false);
  // Track which graph the filter panel targets in compare mode: "primary" | "compare"
  const [filterTarget, setFilterTarget] = useState("primary");
  const setExtraFilters = useCallback((val) => {
    setExtraFiltersRaw(val);
    setFilterChipsSaved(false);
  }, []);
  const setCompareExtraFilters = useCallback((val) => {
    setCompareExtraFiltersRaw(val);
    setFilterChipsSaved(false);
  }, []);
  const metricFilters = useMemo(() => {
    const mf = [];
    if (showErrors) mf.push(FILTER_FOR_ERRORS);
    if (showNonAnnotated) mf.push(FILTER_FOR_NON_ANNOTATED);
    return mf;
  }, [showErrors, showNonAnnotated]);

  // Derive groupBy from selectedTab for display
  const groupBy = selectedTab === "spans" ? "span" : "trace";

  const [selectedCallIds, setSelectedCallIds] = useState([]);
  const { observeId: routeObserveId } = useParams();
  // In user mode there is no project context — observeId is null and all
  // grids/queries omit project_id so the backend scopes by org.
  const observeId = isUserMode ? null : routeObserveId;

  // User mode: sessions routes back out to /dashboard/users/:id — users
  // self-nav is suppressed (we're already on the user). Project mode:
  // cross-nav into observe routes; group-by changes off a saved view
  // also clear the saved-view context so the new destination doesn't
  // inherit its filters.

  // Pulled up out of the larger useObserveHeader() destructure below so
  // handleGroupByChange (which clears activeViewConfig when navigating off
  // a saved view) can reference it without a TDZ. Same context call ID,
  // shape-stable from React.
  const { setActiveViewConfig: setActiveViewConfigFromCtx } =
    useObserveHeader();

  const handleGroupByChange = useCallback(
    (groupKey) => {
      // Group-by changes off a saved view land on the corresponding default
      // tab — the saved view's filters/columns aren't meaningful for a
      // different group key, so we drop activeViewConfig and rewrite the
      // tab URL key. Detect saved view via the URL `tab` (`userTab` in
      // user mode) — `view-<id>` means we're on a custom saved view.
      const params = new URLSearchParams(window.location.search);
      const tabKey = isUserMode ? "userTab" : "tab";
      const onSavedView = params.get(tabKey)?.startsWith("view-");

      switch (groupKey) {
        case "none":
        case "trace":
          if (onSavedView) {
            setActiveViewConfigFromCtx(null);
            if (isUserMode) {
              navigate("?userTab=traces&selectedTab=trace", { replace: true });
            } else {
              navigate(
                `/dashboard/observe/${observeId}/llm-tracing?tab=traces&selectedTab=trace`,
                { replace: true },
              );
            }
          } else {
            setSelectedTab("trace");
          }
          break;
        case "span":
          if (onSavedView) {
            setActiveViewConfigFromCtx(null);
            if (isUserMode) {
              // User Detail has a single "Trace" fixed tab that hosts the
              // selectedTab toggle, so we land on userTab=traces with
              // selectedTab=spans.
              navigate("?userTab=traces&selectedTab=spans", { replace: true });
            } else {
              navigate(
                `/dashboard/observe/${observeId}/llm-tracing?tab=spans&selectedTab=spans`,
                { replace: true },
              );
            }
          } else {
            setSelectedTab("spans");
          }
          break;
        case "users":
          if (!isUserMode) {
            setActiveViewConfigFromCtx(null);
            navigate(`/dashboard/observe/${observeId}/users`);
          }
          break;
        case "sessions":
          if (isUserMode) {
            navigate({
              pathname: `/dashboard/users/${encodeURIComponent(
                userIdForUserMode,
              )}`,
              search: `?${new URLSearchParams({ userTab: "sessions" })}`,
            });
          } else {
            setActiveViewConfigFromCtx(null);
            navigate(`/dashboard/observe/${observeId}/sessions`);
          }
          break;
        default:
          break;
      }
    },
    [
      observeId,
      navigate,
      setSelectedTab,
      setActiveViewConfigFromCtx,
      isUserMode,
      userIdForUserMode,
    ],
  );

  const hiddenGroupByOptions = useMemo(
    () => (isUserMode ? ["users"] : []),
    [isUserMode],
  );

  const [_loading, setLoading] = useState(false);
  const [_latestActive, setLatestActive] = useState(false);
  const graphBoxRef = useRef(null);
  const [_urlRowIndex, setUrlRowIndex, _removeUrlRowIndex] =
    useUrlState("rowIndex");

  const { resetStates, viewMode, setViewMode } = useLLMTracingStoreShallow(
    (state) => ({
      resetStates: state.resetStates,
      viewMode: state.viewMode,
      setViewMode: state.setViewMode,
    }),
  );
  const { selectedTraces, allTracesSelected, totalTraces } =
    useTraceGridStoreShallow((s) => {
      return {
        selectedTraces: s.toggledNodes,
        allTracesSelected: s.selectAll,
        totalTraces: s.totalRowCount,
      };
    });

  const { selectedSpans, allSpansSelected, totalSpans } =
    useSpanGridStoreShallow((s) => {
      return {
        selectedSpans: s.toggledNodes,
        allSpansSelected: s.selectAll,
        totalSpans: s.totalRowCount,
      };
    });

  const {
    openReplaySessionDrawer,
    setIsReplayDrawerCollapsed,
    setCreatedReplay,
    setReplayType,
    setOpenReplaySessionDrawer,
  } = useReplaySessionsStoreShallow((s) => ({
    openReplaySessionDrawer: s.openReplaySessionDrawer,
    setIsReplayDrawerCollapsed: s.setIsReplayDrawerCollapsed,
    setCreatedReplay: s.setCreatedReplay,
    setReplayType: s.setReplayType,
    setOpenReplaySessionDrawer: s.setOpenReplaySessionDrawer,
  }));

  const { mutate: createReplaySessions, isPending: isCreatingReplaySessions } =
    useCreateReplaySessions();

  useEffect(() => {
    // Ensure graph mode defaults to "graph" on mount (not stale agentGraph from URL)
    if (viewMode !== "graph") {
      setViewMode("graph");
    }
    return () => {
      resetStates();
      resetSpanGridStore();
      resetTraceGridStore();
    };
  }, [resetStates]); // eslint-disable-line react-hooks/exhaustive-deps

  // // Initialize graph height
  // useEffect(() => {
  //   const stateGraphHeight = useLLMTracingStore.getState().graphHeight;
  //   if (stateGraphHeight === null) {
  //     const rect = graphBoxRef.current.getBoundingClientRect();
  //     useLLMTracingStore.getState().setGraphHeight(rect.height);
  //   }
  // }, []);

  // const handleMouseDown = useCallback(() => {
  //   useLLMTracingStore.getState().setIsDraggingGraph(true);
  // }, []);

  // const handleMouseMove = useCallback((e) => {
  //   if (useLLMTracingStore.getState().isDraggingGraph) {
  //     const rect = graphBoxRef.current.getBoundingClientRect();
  //     let newHeight = e.clientY - rect.y;
  //     newHeight = Math.max(0, newHeight);
  //     newHeight = Math.round(newHeight);
  //     useLLMTracingStore.getState().setGraphHeight(newHeight);
  //   }
  // }, []);

  // const handleMouseUp = useCallback(() => {
  //   useLLMTracingStore.getState().setIsDraggingGraph(false);
  // }, []);

  // useEffect(() => {
  //   window.addEventListener("mousemove", handleMouseMove);
  //   window.addEventListener("mouseup", handleMouseUp);

  //   return () => {
  //     window.removeEventListener("mousemove", handleMouseMove);
  //     window.removeEventListener("mouseup", handleMouseUp);
  //   };
  // }, [handleMouseMove, handleMouseUp]);
  const [columns, setColumns] = useState({
    "primary-trace": [],
    "compare-trace": [],
    "primary-spans": [],
    "compare-spans": [],
  });

  const handleAddCustomColumns = useCallback(
    (newCols) => {
      const ck = `${selectedGraph}-${selectedTab === "spans" ? "spans" : "trace"}`;
      setColumns((prev) => {
        const existingIds = new Set((prev[ck] || []).map((c) => c.id));
        const deduped = newCols.filter((c) => !existingIds.has(c.id));
        return { ...prev, [ck]: [...(prev[ck] || []), ...deduped] };
      });
    },
    [selectedGraph, selectedTab],
  );

  const handleRemoveCustomColumns = useCallback(
    (removeIds) => {
      const ck = `${selectedGraph}-${selectedTab === "spans" ? "spans" : "trace"}`;
      const removeSet = new Set(removeIds);
      setColumns((prev) => ({
        ...prev,
        [ck]: (prev[ck] || []).filter((c) => !removeSet.has(c.id)),
      }));
    },
    [selectedGraph, selectedTab],
  );

  const [selectedPrimaryInterval, setSelectedPrimaryInterval] = useUrlState(
    "selectedInterval-0",
    "day",
  );
  const [selectedSecondaryInterval, setSelectedSecondaryInterval] = useUrlState(
    "selectedInterval-1",
    "day",
  );

  const { setTestDetailDrawerOpen } = useTestDetailSideDrawerStoreShallow(
    (state) => ({
      setTestDetailDrawerOpen: state.setTestDetailDrawerOpen,
    }),
  );
  const primaryTraceGridRef = useRef(null);
  const compareTraceGridRef = useRef(null);
  const primarySpanGridRef = useRef(null);
  const compareSpanGridRef = useRef(null);
  const primaryCallLogsGridRef = useRef(null);
  const compareCallLogsGridRef = useRef(null);
  const columnConfigureRef = useRef();
  // Drained by onGridReady on the primary grid.
  const pendingColumnStateRef = useRef(null);
  // applyColumnState alone can't persist hide across columnDefs rebuilds —
  // getTraceListColumnDefs sets hide explicitly from col.isVisible, so we
  // need to update col.isVisible in the columns state for hide to stick.
  const pendingHideMapRef = useRef(null);

  const {
    setHeaderConfig,
    activeViewConfig,
    setActiveViewConfig,
    registerGetViewConfig,
    registerGetTabType,
  } = useObserveHeader();

  const { data: projectDetail } = useGetProjectDetails(observeId, !isUserMode);
  // User mode: behave like an OBSERVE project so the many projectSource
  // checks stay on the happy path.
  const projectSource = isUserMode
    ? PROJECT_SOURCE.OBSERVE
    : projectDetail?.source;
  const defaultDateFilter = useMemo(() => getDefaultDateRange(), []);

  const [isPrimaryFilterOpen, setIsPrimaryFilterOpen] = useUrlState(
    `isFilterOpen-primary`,
    false,
  );
  const [externalFilterAnchor, setExternalFilterAnchor] = useState(null);

  const handleCompareFilterToggle = useCallback(
    (e, target = "primary") => {
      if (isPrimaryFilterOpen) {
        setIsPrimaryFilterOpen(false);
        setExternalFilterAnchor(null);
      } else {
        setFilterTarget(target);
        setExternalFilterAnchor(e?.currentTarget || null);
        setIsPrimaryFilterOpen(true);
      }
    },
    [isPrimaryFilterOpen, setIsPrimaryFilterOpen],
  );

  useEffect(() => {
    // Reset column state when observeId changes
    setAutoSizeAllCols(false);

    const gridRef =
      selectedTab === "trace" ? primaryTraceGridRef : primarySpanGridRef;

    if (gridRef?.current?.api) {
      gridRef.current.api.sizeColumnsToFit();
    }
  }, [observeId]);

  const handleAutoSize = () => {
    const gridRef =
      selectedGraph === "primary"
        ? selectedTab === "trace"
          ? primaryTraceGridRef
          : primarySpanGridRef
        : selectedTab === "trace"
          ? compareTraceGridRef
          : compareSpanGridRef;

    if (!gridRef.current?.api) return;

    const gridApi = gridRef.current.api;

    if (!gridApi.isAnimationFrameQueueEmpty?.()) return;

    const rowCount = gridApi.getDisplayedRowCount();
    if (rowCount === 0) return;

    const rowData = [];
    gridApi.forEachNode((node) => rowData.push(node.data));
    if (rowData.length === 0 || rowData.every((row) => !row)) return;

    if (!autoSizeAllCols) {
      setAutoSizeAllCols(true);
      const allDisplayedColumns = gridApi.getAllDisplayedColumns();

      const columnIdsToAutoSize = allDisplayedColumns.map((col) =>
        col.getColId(),
      );

      if (columnIdsToAutoSize.length > 0) {
        gridApi.autoSizeColumns(columnIdsToAutoSize, false);
      }
    } else {
      setAutoSizeAllCols(false);
      gridApi.sizeColumnsToFit();
    }
  };

  const resetColumns = () => {
    setAutoSizeAllCols(false);
  };

  const defaultFilter = useMemo(() => getDefaultFilter(), []);

  const {
    filters: primaryTraceFilters,
    setFilters: setPrimaryTraceFilters,
    validatedFilters: primaryTraceValidatedFiltersRaw,
    setDateFilter: setPrimaryTraceDateFilter,
    dateFilter: primaryTraceDateFilter,
  } = useLLMTracingFilters(
    defaultFilter,
    defaultDateFilter,
    "primaryTraceFilter",
    "primaryTraceDateFilter",
    columns["primary-trace"],
    getFilterExtraProperties,
  );

  const {
    filters: primarySpanFilters,
    setFilters: setPrimarySpanFilters,
    validatedFilters: primarySpanValidatedFiltersRaw,
    setDateFilter: setPrimarySpanDateFilter,
    dateFilter: primarySpanDateFilter,
  } = useLLMTracingFilters(
    defaultFilter,
    defaultDateFilter,
    "primarySpanFilter",
    "primarySpanDateFilter",
    columns["primary-spans"],
    getFilterExtraProperties,
  );

  const {
    filters: compareTraceFilters,
    setFilters: setCompareTraceFilters,
    validatedFilters: compareTraceValidatedFiltersRaw,
    setDateFilter: setCompareTraceDateFilter,
    dateFilter: compareTraceDateFilter,
  } = useLLMTracingFilters(
    defaultFilter,
    defaultDateFilter,
    "compareTraceFilter",
    "compareTraceDateFilter",
    columns["compare-trace"],
    getFilterExtraProperties,
  );

  const {
    filters: compareSpansFilters,
    setFilters: setCompareSpansFilters,
    validatedFilters: compareSpansValidatedFiltersRaw,
    setDateFilter: setCompareSpansDateFilter,
    dateFilter: compareSpansDateFilter,
  } = useLLMTracingFilters(
    defaultFilter,
    defaultDateFilter,
    "compareSpansFilter",
    "compareSpansDateFilter",
    columns["compare-spans"],
    getFilterExtraProperties,
  );

  // User mode injects a structural user_id filter into the primary filters
  // (not extraFilters, so it doesn't render as a removable chip).
  const userScopeFilter = useMemo(
    () =>
      isUserMode && userIdForUserMode
        ? [
            {
              columnId: "user_id",
              filterConfig: {
                filterType: "text",
                filterOp: "equals",
                filterValue: userIdForUserMode,
              },
            },
          ]
        : [],
    [isUserMode, userIdForUserMode],
  );
  const primaryTraceValidatedFilters = useMemo(
    () => [...userScopeFilter, ...primaryTraceValidatedFiltersRaw],
    [userScopeFilter, primaryTraceValidatedFiltersRaw],
  );
  const primarySpanValidatedFilters = useMemo(
    () => [...userScopeFilter, ...primarySpanValidatedFiltersRaw],
    [userScopeFilter, primarySpanValidatedFiltersRaw],
  );

  // Drop filter-mode opt-in when the selection becomes inconsistent (grid
  // cleared, tab switched, project changed, filter changed). User can
  // re-opt-in via the banner. Mirrored for spans below.
  useEffect(() => {
    if (!allTracesSelected) setFilterSelectionMode(false);
  }, [allTracesSelected]);
  useEffect(() => {
    if (!allSpansSelected) setSpanFilterSelectionMode(false);
  }, [allSpansSelected]);
  useEffect(() => {
    setFilterSelectionMode(false);
    setSpanFilterSelectionMode(false);
  }, [selectedTab, observeId]);
  useEffect(() => {
    setSpanFilterSelectionMode(false);
  }, [primarySpanValidatedFilters]);
  // Simulator: reset filter-mode when page-full-selection breaks.
  useEffect(() => {
    if (!simCallMeta.isAllOnPageSelected) {
      setSimCallFilterSelectionMode(false);
    }
  }, [simCallMeta.isAllOnPageSelected]);
  const compareTraceValidatedFilters = useMemo(
    () => [...userScopeFilter, ...compareTraceValidatedFiltersRaw],
    [userScopeFilter, compareTraceValidatedFiltersRaw],
  );
  const compareSpansValidatedFilters = useMemo(
    () => [...userScopeFilter, ...compareSpansValidatedFiltersRaw],
    [userScopeFilter, compareSpansValidatedFiltersRaw],
  );

  // Agent graph data — fetched when viewMode is agentGraph or agentPath.
  // Disabled entirely in user mode (no project context to scope to).
  const {
    data: agentGraphData,
    isLoading: isAgentGraphLoading,
    isError: isAgentGraphError,
  } = useAgentGraph(
    observeId,
    selectedTab === "trace"
      ? primaryTraceValidatedFilters
      : primarySpanValidatedFilters,
    {
      enabled:
        !isUserMode && (viewMode === "agentGraph" || viewMode === "agentPath"),
    },
  );

  // Compare agent graph data — only fetched in compare mode
  const {
    data: compareAgentGraphData,
    isLoading: isCompareAgentGraphLoading,
    isError: isCompareAgentGraphError,
  } = useAgentGraph(
    observeId,
    selectedTab === "trace"
      ? compareTraceValidatedFilters
      : compareSpansValidatedFilters,
    {
      enabled:
        !isUserMode &&
        showCompare &&
        (viewMode === "agentGraph" || viewMode === "agentPath"),
    },
  );

  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", observeId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          filters: JSON.stringify({ project_id: observeId }),
        },
      }),
    select: (data) => data.data?.result,
    enabled: Boolean(observeId),
  });

  // Shared node click handler for agent graph/path views
  const handleAgentNodeClick = useCallback(
    (nodeData) => {
      if (!nodeData?.type) return;
      const isSame = extraFilters.some(
        (f) =>
          f.column_id === "observation_type" &&
          f.filter_config?.filter_value === nodeData.type,
      );
      if (isSame) {
        setExtraFilters([]);
      } else {
        setExtraFilters([
          {
            column_id: "observation_type",
            filter_config: {
              filter_type: "string",
              filter_op: "equals",
              filter_value: nodeData.type,
            },
          },
        ]);
      }
    },
    [extraFilters, setExtraFilters],
  );

  const handleSimulatorConfigLoaded = useCallback(
    (config) => {
      if (projectSource === PROJECT_SOURCE.SIMULATOR && config?.length > 0) {
        setColumns((prev) => {
          // Voice projects use CallLogsGrid (no per-fetch merge to drain
          // pending custom cols), so this callback is the only path that
          // drains them on backend column-count changes.
          const drainPending = (key, ref) => {
            const existing = prev[key] || [];
            const customCols = existing.filter(
              (c) => c.groupBy === "Custom Columns",
            );
            const pending = ref?.current || [];
            const existingIds = new Set(customCols.map((c) => c.id));
            const dedupedPending = pending.filter(
              (c) => !existingIds.has(c.id),
            );
            if (pending.length > 0 && ref) {
              ref.current = [];
            }
            return [...config, ...customCols, ...dedupedPending];
          };
          return {
            ...prev,
            "primary-trace": drainPending(
              "primary-trace",
              primaryTracePendingRef,
            ),
            "compare-trace": drainPending(
              "compare-trace",
              compareTracePendingRef,
            ),
          };
        });
      }
    },
    [projectSource],
  );

  const queryClient = useQueryClient();

  const [selectedPrimaryGraphProperty, _setSelectedPrimaryGraphProperty] =
    useUrlState("selectedPrimaryGraphProperty", "");

  const setSelectedPrimaryGraphProperty = (property) => {
    trackEvent(Events.observeGraphPropertySelected, {
      [PropertyName.metric]: property,
      [PropertyName.graphType]: "primary",
    });

    _setSelectedPrimaryGraphProperty(property);
  };

  const [selectedPrimaryGraphEvals, setSelectedPrimaryGraphEvals] = useUrlState(
    "selectedPrimaryGraphEvals",
    [],
  );

  const [selectedCompareGraphProperty, _setSelectedCompareGraphProperty] =
    useUrlState("selectedCompareGraphProperty", "");

  const setSelectedCompareGraphProperty = (property) => {
    trackEvent(Events.observeGraphPropertySelected, {
      [PropertyName.metric]: property,
      [PropertyName.graphType]: "compare",
    });

    _setSelectedCompareGraphProperty(property);
  };

  const [selectedCompareGraphEvals, setSelectedCompareGraphEvals] = useUrlState(
    "selectedCompareGraphEvals",
    [],
  );

  const [selectedPrimaryGraphAttributes, setSelectedPrimaryGraphAttributes] =
    useUrlState("selectedPrimaryGraphAttributes", []);

  const [selectedCompareGraphAttributes, setSelectedCompareGraphAttributes] =
    useUrlState("selectedCompareGraphAttributes", []);

  const theme = useTheme();

  // Combine drawer filters with graph filters for simulator projects
  const primaryCombinedFilters = useMemo(() => {
    const graphFilters = convertGraphSelectionsToFilters(
      selectedPrimaryGraphEvals,
      selectedPrimaryGraphAttributes,
      columns["primary-trace"],
    );
    return [...primaryTraceValidatedFilters, ...graphFilters];
  }, [
    primaryTraceValidatedFilters,
    selectedPrimaryGraphEvals,
    selectedPrimaryGraphAttributes,
    columns,
  ]);

  // Watch combined filters (validated + graph) — both POSTs send the
  // combined payload, so the validated subset alone would miss graph toggles.
  useEffect(() => {
    setFilterSelectionMode(false);
  }, [primaryCombinedFilters]);
  useEffect(() => {
    setSimCallFilterSelectionMode(false);
  }, [observeId, primaryCombinedFilters]);

  const compareCombinedFilters = useMemo(() => {
    const graphFilters = convertGraphSelectionsToFilters(
      selectedCompareGraphEvals,
      selectedCompareGraphAttributes,
      columns["compare-trace"],
    );
    return [...compareTraceValidatedFilters, ...graphFilters];
  }, [
    compareTraceValidatedFilters,
    selectedCompareGraphEvals,
    selectedCompareGraphAttributes,
    columns,
  ]);

  const setSpecificColumns = (key, columns) => {
    setColumns((prev) => ({ ...prev, [key]: columns }));
  };

  const resetFilters = useCallback(() => {
    setPrimaryTraceFilters(defaultFilter);
    setPrimarySpanFilters(defaultFilter);
    setCompareTraceFilters(defaultFilter);
    setCompareSpansFilters(defaultFilter);
  }, [
    defaultFilter,
    setPrimaryTraceFilters,
    setPrimarySpanFilters,
    setCompareTraceFilters,
    setCompareSpansFilters,
  ]);

  useEffect(() => {
    trackEvent(Events.pObserveFilterApplied, {
      [PropertyName.formFields]: {
        compareTraceValidatedFilters,
        primaryTraceValidatedFilters,
        primarySpanValidatedFilters,
        compareSpansValidatedFilters,
      },
    });
  }, [
    compareTraceValidatedFilters,
    primaryTraceValidatedFilters,
    primarySpanValidatedFilters,
    compareSpansValidatedFilters,
  ]);

  const [attributes, setAttributes] = useState([]);

  useEffect(() => {
    setAttributes(evalAttributes || []);
  }, [evalAttributes]);

  // User mode only — project mode already scopes to a single project.
  const projectFilterField = useProjectFilterField({ enabled: isUserMode });
  const hasAnnotatorFilter = useMemo(
    () =>
      [...(extraFilters || []), ...(compareExtraFilters || [])].some(
        (filter) => filter?.column_id === "annotator",
      ),
    [extraFilters, compareExtraFilters],
  );
  const { data: annotatorFilterOptions = [] } = useDashboardFilterValues({
    metricName: "annotator",
    metricType: "annotation_metric",
    projectIds: observeId ? [observeId] : [],
    // Keep this in sync with the TraceFilterPanel ValuePicker source so
    // applying a freshly-picked annotator can reuse the same cached options.
    source: "traces",
    enabled: hasAnnotatorFilter,
  });
  const annotatorFilterLabelMap = useMemo(() => {
    const entries = annotatorFilterOptions
      .map((option) => {
        const value = String(getPickerOptionValue(option));
        if (!value) return null;
        const label = getPickerOptionLabel(option);
        const email = getPickerOptionSecondaryLabel(option);
        return [value, email ? `${label} (${email})` : label];
      })
      .filter(Boolean);
    return entries.length > 0 ? Object.fromEntries(entries) : null;
  }, [annotatorFilterOptions]);
  const toolbarFilterFields = useMemo(
    () => (projectFilterField ? [projectFilterField] : undefined),
    [projectFilterField],
  );
  // Map a filter's raw value back to the human label for chip display.
  const filterChipLabelMap = useMemo(() => {
    const map = {};
    if (projectFilterField?.choices?.length) {
      map.project_id = Object.fromEntries(
        projectFilterField.choices.map((c) => [c.value, c.label]),
      );
    }
    if (annotatorFilterLabelMap) {
      map.annotator = annotatorFilterLabelMap;
    }
    return Object.keys(map).length > 0 ? map : undefined;
  }, [projectFilterField, annotatorFilterLabelMap]);

  const [primaryFilterDefinition, setPrimaryFilterDefinition] = useState(() => {
    if (selectedTab === "trace") {
      return generateObserveTraceFilterDefinition(
        columns["primary-trace"],
        attributes,
        null,
        projectSource,
      );
    }
    return generateSpanObserveFilterDefinition(
      columns["primary-spans"],
      attributes,
    );
  });

  const [compareFilterDefinition, setCompareFilterDefinition] = useState(() => {
    if (selectedTab === "trace") {
      return generateObserveTraceFilterDefinition(
        columns["compare-trace"],
        attributes,
        null,
        projectSource,
      );
    }
    return generateSpanObserveFilterDefinition(
      columns["compare-trace"],
      attributes,
    );
  });

  // Memoized helper for preserving attribute definitions
  const preserveAttributeDefinitions = useMemo(() => {
    return (prevDefinition, newBaseDefinition) => {
      const attributionIndex = prevDefinition?.findIndex(
        (item) => item?.propertyName === "Attribute",
      );

      // Only preserve the old Attribute block if the user has active
      // attribute filters — otherwise always use the fresh definition
      // so that enriched type info (number/boolean) from the API is applied.
      const prevAttrBlock = prevDefinition?.[attributionIndex];
      const hasUserFilters =
        Array.isArray(prevAttrBlock?.selectedDependents) &&
        prevAttrBlock.selectedDependents.length > 0;

      if (hasUserFilters) {
        // Preserve user's active attribute filter selections
        const copy = [...newBaseDefinition];
        const copyAttributionIndex = copy?.findIndex(
          (item) => item?.propertyName === "Attribute",
        );
        if (copyAttributionIndex >= 0) {
          copy[copyAttributionIndex] = prevAttrBlock;
        }
        return copy;
      } else {
        // Generate fresh with enriched types from API
        return newBaseDefinition;
      }
    };
  }, []);

  useEffect(() => {
    if (selectedTab === "trace") {
      setPrimaryFilterDefinition((prevDefinition) => {
        const newBaseDefinition = generateObserveTraceFilterDefinition(
          columns["primary-trace"],
          attributes,
          primaryTraceFilters,
          projectSource,
        );
        return preserveAttributeDefinitions(prevDefinition, newBaseDefinition);
      });
    } else {
      setPrimaryFilterDefinition((prevDefinition) => {
        const newBaseDefinition = generateSpanObserveFilterDefinition(
          columns["primary-spans"],
          attributes,
          primarySpanFilters,
        );
        return preserveAttributeDefinitions(prevDefinition, newBaseDefinition);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTab, columns, attributes, preserveAttributeDefinitions]);

  useEffect(() => {
    if (selectedTab === "trace") {
      setCompareFilterDefinition((prevDefinition) => {
        const newBaseDefinition = generateObserveTraceFilterDefinition(
          columns["compare-trace"],
          attributes,
          compareTraceFilters,
          projectSource,
        );
        return preserveAttributeDefinitions(prevDefinition, newBaseDefinition);
      });
    } else {
      setCompareFilterDefinition((prevDefinition) => {
        const newBaseDefinition = generateSpanObserveFilterDefinition(
          columns["compare-spans"],
          attributes,
          compareSpansFilters,
        );
        return preserveAttributeDefinitions(prevDefinition, newBaseDefinition);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTab, columns, attributes, preserveAttributeDefinitions]);

  // const compareFilterDefinition = useMemo(() => {
  //   if (selectedTab === "trace") {
  //     return generateObserveTraceFilterDefinition(columns["compare-trace"]);
  //   }
  //   return generateSpanObserveFilterDefinition(columns["compare-spans"]);
  // }, [selectedTab, columns]);

  const refreshPrimary = useCallback(
    (setLoading = true) => {
      logger.debug("refreshPrimary", { setLoading });
      if (setLoading) {
        setLatestActive(true);
      }
      trackEvent(Events.pObserveRefreshClicked);
      if (projectSource === PROJECT_SOURCE.SIMULATOR) {
        queryClient.invalidateQueries({
          queryKey: ["callLogs"],
        });
      } else {
        if (primaryTraceGridRef.current) {
          primaryTraceGridRef.current.api.refreshServerSide();
        }
        if (primarySpanGridRef.current) {
          primarySpanGridRef.current.api.refreshServerSide();
        }
        queryClient.invalidateQueries({
          queryKey: ["llm-tracing-graph"],
        });
      }
    },
    [queryClient, projectSource],
  );

  const refreshCompare = useCallback(
    (setLoading = true) => {
      logger.debug("refreshCompare", { setLoading });
      if (setLoading) {
        setLatestActive(true);
      }
      trackEvent(Events.pObserveRefreshClicked);
      if (compareTraceGridRef.current) {
        compareTraceGridRef.current.api.refreshServerSide();
      }

      if (compareSpanGridRef.current) {
        compareSpanGridRef.current.api.refreshServerSide();
      }

      queryClient.invalidateQueries({
        queryKey: ["llm-tracing-graph"],
      });
    },
    [queryClient],
  );

  const refreshAll = useCallback(() => {
    setLatestActive(true);
    refreshPrimary(false);
    refreshCompare(false);
  }, [refreshCompare, refreshPrimary]);

  const columnKey = useMemo(() => {
    if (selectedGraph === "primary" && selectedTab === "trace") {
      return "primary-trace";
    }
    if (selectedGraph === "primary" && selectedTab === "spans") {
      return "primary-spans";
    }
    if (selectedGraph === "compare" && selectedTab === "trace") {
      return "compare-trace";
    }
    if (selectedGraph === "compare" && selectedTab === "spans") {
      return "compare-spans";
    }
    return "";
  }, [selectedGraph, selectedTab]);

  const onColumnVisibilityChange = (updatedData) => {
    setColumns((cols) => {
      const newCols =
        cols[columnKey]?.map((col) => ({
          ...col,
          isVisible: updatedData[col.id],
        })) || [];

      return {
        ...cols,
        [columnKey]: newCols,
      };
    });

    // updateProjectColumnVisibility(updatedData);
  };

  // const { mutate: updateProjectColumnVisibility } = useMutation({
  //   mutationFn: (data) =>
  //     axios.post(endpoints.project.updateProjectColumnVisibility(), {
  //       projectId: observeId,
  //       visibility: data,
  //     }),
  // });

  const handleRowClicked = (params, page, pageLimit) => {
    const localIndex = params.rowIndex;
    const globalIndex = (page - 1) * pageLimit + localIndex;
    setUrlRowIndex({
      rowIndex: globalIndex,
      origin: "project",
      module: "project",
    });
    setTestDetailDrawerOpen({
      ...params?.data,
      ignoreCache: true,
    });
  };

  useEffect(() => {
    // In user mode the page lives outside the observe shell — its parent
    // (CrossProjectUserDetailPage) renders its own header.
    if (isUserMode) return;
    setHeaderConfig({
      text: "LLM Tracing",
      filterTrace: primaryTraceValidatedFilters,
      filterSpan: primarySpanValidatedFilters,
      selectedTab: selectedTab,
      refreshData: refreshAll,
      resetFilters: resetFilters,
    });
  }, [
    isUserMode,
    selectedTab,
    refreshAll,
    setHeaderConfig,
    primaryTraceValidatedFilters,
    primarySpanValidatedFilters,
    resetFilters,
  ]);

  const setLoadingEnhanced = useCallback(
    (v) => {
      setLoading(v);
      if (!v) {
        setLatestActive(v);
      }
    },
    [setLoading, setLatestActive],
  );

  // Date label for the toolbar — derive from current date interval URL param
  const dateLabel = useMemo(() => {
    const raw =
      selectedTab === "trace" ? primaryTraceDateFilter : primarySpanDateFilter;
    // dateFilter is { dateFilter: [start, end], dateOption: "6M" }
    const option = raw?.dateOption;
    if (option && option !== "Custom") {
      const labels = {
        Today: "Today",
        Yesterday: "Yesterday",
        "7D": "Past 7D",
        "30D": "Past 30D",
        "3M": "Past 3M",
        "6M": "Past 6M",
        "12M": "Past 12M",
      };
      return labels[option] || `Past ${option}`;
    }
    // Custom range — show dates
    const dates = raw?.dateFilter;
    if (!dates || dates.length < 2) return "Past 6M";
    try {
      const start = new Date(dates[0]);
      const end = new Date(dates[1]);
      if (isNaN(start.getTime()) || isNaN(end.getTime())) return "Past 6M";
      return `${start.toLocaleDateString()} - ${end.toLocaleDateString()}`;
    } catch {
      return "Past 6M";
    }
  }, [selectedTab, primaryTraceDateFilter, primarySpanDateFilter]);

  // wasOnSavedViewRef gates the null-branch reset to genuine saved-view →
  // default transitions so it doesn't clobber state that the mount hydrate
  // or apply branch is about to set.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const wasOnSavedViewRef = useRef(false);
  useEffect(() => {
    if (!activeViewConfig) {
      const wasOnSavedView = wasOnSavedViewRef.current;
      wasOnSavedViewRef.current = false;
      if (!wasOnSavedView) return;
      // Back to default tab — viewMode lives in Zustand (no URL key) so
      // reset explicitly; AG Grid columnState also needs an imperative reset.
      setExtraFilters((prev) => (prev.length === 0 ? prev : []));
      setViewMode(DEFAULT_DISPLAY_CONFIG.viewMode);
      pendingColumnStateRef.current = null;
      pendingHideMapRef.current = null;
      primaryTracePendingRef.current = [];
      compareTracePendingRef.current = [];
      primarySpansPendingRef.current = [];
      compareSpansPendingRef.current = [];
      // Strip saved-view customs and reset isVisible on the remaining cols —
      // TraceGrid's merge preserves isVisible across fetches, so leaving a
      // view that hid columns would otherwise persist the hide state.
      setColumns((prev) => {
        const next = {};
        Object.keys(prev).forEach((ck) => {
          next[ck] = (prev[ck] || [])
            .filter((c) => c.groupBy !== "Custom Columns")
            .map((c) => (c.isVisible ? c : { ...c, isVisible: true }));
        });
        return next;
      });
      // Re-hydrate from localStorage — the mount hydrate is keyed on
      // displayStorageKey and won't re-fire on a same-project tab toggle.
      try {
        const raw = localStorage.getItem(displayStorageKey);
        const saved = raw ? JSON.parse(raw) : null;
        if (saved?.customColumns) {
          // customColumns may be a legacy array or new {trace, spans}
          // object. Hydrate both primary and compare refs for the tab
          // type so a later compare-mode toggle works without another
          // localStorage read.
          const cloneEach = (arr) => arr.map((c) => ({ ...c }));
          if (Array.isArray(saved.customColumns)) {
            if (saved.customColumns.length > 0) {
              if (selectedTab === "trace") {
                primaryTracePendingRef.current = cloneEach(saved.customColumns);
                compareTracePendingRef.current = cloneEach(saved.customColumns);
              } else {
                primarySpansPendingRef.current = cloneEach(saved.customColumns);
                compareSpansPendingRef.current = cloneEach(saved.customColumns);
              }
            }
          } else {
            const traceCols = saved.customColumns.trace || [];
            const spansCols = saved.customColumns.spans || [];
            if (traceCols.length > 0) {
              primaryTracePendingRef.current = cloneEach(traceCols);
              compareTracePendingRef.current = cloneEach(traceCols);
            }
            if (spansCols.length > 0) {
              primarySpansPendingRef.current = cloneEach(spansCols);
              compareSpansPendingRef.current = cloneEach(spansCols);
            }
          }
        }
      } catch {
        /* ignore corrupted localStorage */
      }
      // Voice/simulator: handleSimulatorConfigLoaded only fires on column-
      // count changes, so saved-view → default transitions need an explicit
      // drain here.
      if (projectSource === PROJECT_SOURCE.SIMULATOR) {
        const draining = primaryTracePendingRef.current || [];
        if (draining.length > 0) {
          setColumns((prev) => {
            const merge = (key) => {
              const existing = prev[key] || [];
              const stripped = existing.filter(
                (c) => c.groupBy !== "Custom Columns",
              );
              return [...stripped, ...draining];
            };
            return {
              ...prev,
              "primary-trace": merge("primary-trace"),
              "compare-trace": merge("compare-trace"),
            };
          });
          primaryTracePendingRef.current = [];
          compareTracePendingRef.current = [];
        }
      }
      const activeApi =
        selectedTab === "trace"
          ? primaryTraceGridRef.current?.api
          : primarySpanGridRef.current?.api;
      if (activeApi?.resetColumnState) activeApi.resetColumnState();
      return;
    }
    wasOnSavedViewRef.current = true;

    // Apply display settings
    const display = activeViewConfig.display || {};
    if (display.viewMode) setViewMode(display.viewMode);
    if (display.cellHeight) setCellHeight(display.cellHeight);
    if (display.showErrors !== undefined) setShowErrors(display.showErrors);
    if (display.showNonAnnotated !== undefined)
      setShowNonAnnotated(display.showNonAnnotated);
    if (display.showCompare !== undefined) setShowCompare(display.showCompare);
    if (display.hasEvalFilter !== undefined)
      setHasEvalFilter(display.hasEvalFilter);

    // Strip existing customs so view → view doesn't show the union of both
    // sets (which would also dirty-flag the Save view button).
    setColumns((prev) => {
      const next = {};
      Object.keys(prev).forEach((ck) => {
        next[ck] = (prev[ck] || []).filter(
          (c) => c.groupBy !== "Custom Columns",
        );
      });
      return next;
    });

    // Populate both primary and compare refs for the active tab type so a
    // compare-mode toggle later hydrates correctly. Shallow-clone per slot
    // so mutations don't write through into the saved-views query cache.
    if (display.customColumns?.length > 0) {
      if (selectedTab === "trace") {
        primaryTracePendingRef.current = display.customColumns.map((c) => ({
          ...c,
        }));
        compareTracePendingRef.current = display.customColumns.map((c) => ({
          ...c,
        }));
      } else {
        primarySpansPendingRef.current = display.customColumns.map((c) => ({
          ...c,
        }));
        compareSpansPendingRef.current = display.customColumns.map((c) => ({
          ...c,
        }));
      }
    }

    // Voice/simulator: same-tab-type saved-view switch doesn't trigger
    // handleSimulatorConfigLoaded, so drain into columns directly.
    if (
      projectSource === PROJECT_SOURCE.SIMULATOR &&
      display.customColumns?.length > 0
    ) {
      setColumns((prev) => {
        const merge = (key) => {
          const existing = prev[key] || [];
          const stripped = existing.filter(
            (c) => c.groupBy !== "Custom Columns",
          );
          const fresh = display.customColumns.map((c) => ({ ...c }));
          return [...stripped, ...fresh];
        };
        return {
          ...prev,
          "primary-trace": merge("primary-trace"),
          "compare-trace": merge("compare-trace"),
        };
      });
      // Clear pending refs so handleSimulatorConfigLoaded doesn't drain
      // them again on a later config callback.
      primaryTracePendingRef.current = [];
      compareTracePendingRef.current = [];
    }

    // Hide needs a parallel path: applyColumnState's hide doesn't survive
    // the next columnDefs rebuild (getTraceListColumnDefs sets hide from
    // col.isVisible, which wins over applied state). The [columns] drain
    // effect below updates col.isVisible from this map.
    if (Array.isArray(display.columnState) && display.columnState.length > 0) {
      const hideMap = {};
      display.columnState.forEach((entry) => {
        if (entry && entry.colId) hideMap[entry.colId] = !!entry.hide;
      });
      // Apply hideMap immediately so view→view switches with identical
      // backend cols (no merge → no drain) still pick up the hide intent.
      setColumns((prev) => {
        let anyChanged = false;
        const next = {};
        Object.keys(prev).forEach((ck) => {
          let slotChanged = false;
          const updated = (prev[ck] || []).map((col) => {
            if (col && col.id in hideMap) {
              const desiredVisible = !hideMap[col.id];
              if (col.isVisible !== desiredVisible) {
                slotChanged = true;
                return { ...col, isVisible: desiredVisible };
              }
            }
            return col;
          });
          next[ck] = slotChanged ? updated : prev[ck];
          if (slotChanged) anyChanged = true;
        });
        return anyChanged ? next : prev;
      });
      // Queue for cols that arrive later via TraceGrid's merge.
      pendingHideMapRef.current = hideMap;

      const activeApi =
        selectedTab === "trace"
          ? primaryTraceGridRef.current?.api
          : primarySpanGridRef.current?.api;
      if (activeApi?.applyColumnState) {
        activeApi.applyColumnState({
          state: display.columnState,
          applyOrder: true,
        });
      } else {
        pendingColumnStateRef.current = display.columnState;
      }
    }

    // dateFilter lives inside display because the backend serializer only
    // whitelists `display` for arbitrary sub-keys.
    if (display.dateFilter) {
      if (selectedTab === "trace") {
        setPrimaryTraceDateFilter(display.dateFilter);
      } else {
        setPrimarySpanDateFilter(display.dateFilter);
      }
    }

    // Array.isArray guard: UsersView writes `filters` as an object, which
    // can briefly leak during cross-tab transitions (route change is queued
    // in startTransition while activeViewConfig updates synchronously).
    const rawFilters = activeViewConfig.filters;
    const nextFilters = (Array.isArray(rawFilters) ? rawFilters : []).map(
      (f) => ({
        ...f,
        id: f.id || getRandomId(),
      }),
    );
    if (selectedTab === "trace") {
      setPrimaryTraceFilters(nextFilters);
    } else {
      setPrimarySpanFilters(nextFilters);
    }

    // Apply extraFilters unconditionally (independent of compare mode).
    setExtraFilters(
      Array.isArray(activeViewConfig.extraFilters)
        ? activeViewConfig.extraFilters
        : [],
    );

    // Compare state — always replace, regardless of current showCompare state.
    const rawCompareFilters = activeViewConfig.compareFilters;
    const nextCompareFilters = (
      Array.isArray(rawCompareFilters) ? rawCompareFilters : []
    ).map((f) => ({
      ...f,
      id: f.id || getRandomId(),
    }));
    if (selectedTab === "trace") {
      setCompareTraceFilters(nextCompareFilters);
      if (activeViewConfig.compareDateFilter !== undefined) {
        setCompareTraceDateFilter(activeViewConfig.compareDateFilter);
      }
    } else {
      setCompareSpansFilters(nextCompareFilters);
      if (activeViewConfig.compareDateFilter !== undefined) {
        setCompareSpansDateFilter(activeViewConfig.compareDateFilter);
      }
    }
    setCompareExtraFilters(activeViewConfig.compareExtraFilters || []);

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeViewConfig]);

  // Drains pendingColumnStateRef once the lazy-loaded grid mounts.
  useEffect(() => {
    if (!pendingColumnStateRef.current) return;
    let attempts = 0;
    let timer = null;
    const tryApply = () => {
      const api =
        selectedTab === "trace"
          ? primaryTraceGridRef.current?.api
          : primarySpanGridRef.current?.api;
      if (
        api?.applyColumnState &&
        Array.isArray(pendingColumnStateRef.current)
      ) {
        api.applyColumnState({
          state: pendingColumnStateRef.current,
          applyOrder: true,
        });
        pendingColumnStateRef.current = null;
        return;
      }
      if (attempts++ < 20) {
        timer = setTimeout(tryApply, 100);
      }
    };
    tryApply();
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [activeViewConfig, selectedTab]);

  // Re-apply queued columnState + hideMap once `columns` updates. The
  // retry effect above only fires on activeViewConfig/selectedTab change;
  // if it ran before custom cols landed, AG Grid dropped their entries.
  // The hideMap path is necessary because the next columnDefs rebuild
  // overrides applyColumnState's hide flag from col.isVisible.
  useEffect(() => {
    if (pendingHideMapRef.current) {
      const hideMap = pendingHideMapRef.current;
      // Only clear pendingHideMapRef if at least one col matched —
      // otherwise a cold-load drain on empty slots would wipe the queue
      // before TraceGrid/SpanGrid's first setColumns lands the cols.
      let sawMatch = false;
      setColumns((prev) => {
        let anyChanged = false;
        const next = {};
        Object.keys(prev).forEach((ck) => {
          let slotChanged = false;
          const updated = (prev[ck] || []).map((col) => {
            if (col && col.id in hideMap) {
              sawMatch = true;
              const desiredVisible = !hideMap[col.id];
              if (col.isVisible !== desiredVisible) {
                slotChanged = true;
                return { ...col, isVisible: desiredVisible };
              }
            }
            return col;
          });
          next[ck] = slotChanged ? updated : prev[ck];
          if (slotChanged) anyChanged = true;
        });
        return anyChanged ? next : prev;
      });
      if (sawMatch) {
        pendingHideMapRef.current = null;
      }
    }
    if (!pendingColumnStateRef.current) return;
    const api =
      selectedTab === "trace"
        ? primaryTraceGridRef.current?.api
        : primarySpanGridRef.current?.api;
    if (!api?.applyColumnState) return;
    api.applyColumnState({
      state: pendingColumnStateRef.current,
      applyOrder: true,
    });
    pendingColumnStateRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns]);

  // ---------------------------------------------------------------------------
  // View persistence — auto-save display + reset/default
  // ---------------------------------------------------------------------------
  const displayStorageKey = isUserMode
    ? `user-display-${userIdForUserMode}`
    : `observe-display-${observeId}`;
  const filtersStorageKey = isUserMode
    ? `user-filters-${userIdForUserMode}`
    : `observe-filters-${observeId}`;

  // Pending custom cols, queued until the backend returns real columns so
  // the grid doesn't render with only-custom-col headers mid-load. One ref
  // per grid instance — a shared ref would race across the 4 mounted grids.
  const primaryTracePendingRef = useRef([]);
  const compareTracePendingRef = useRef([]);
  const primarySpansPendingRef = useRef([]);
  const compareSpansPendingRef = useRef([]);

  // Mount hydrate for the default tab. Saved-view tabs hydrate via the
  // apply effect above; seeding here on a saved-view URL would drain the
  // wrong customs before the view config arrives.
  useEffect(() => {
    if (activeViewTabId) return;
    try {
      const raw = localStorage.getItem(displayStorageKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.viewMode) setViewMode(saved.viewMode);
      if (saved.cellHeight) setCellHeight(saved.cellHeight);
      if (saved.showErrors) setShowErrors(saved.showErrors);
      if (saved.showNonAnnotated) setShowNonAnnotated(saved.showNonAnnotated);
      if (saved.showCompare) setShowCompare(saved.showCompare);
      if (saved.hasEvalFilter) setHasEvalFilter(saved.hasEvalFilter);
      // Accept both new {trace, spans} object shape and legacy flat array
      // (treated as customs for the current tab only).
      if (saved.customColumns) {
        const cloneEach = (arr) => arr.map((c) => ({ ...c }));
        if (Array.isArray(saved.customColumns)) {
          if (saved.customColumns.length > 0) {
            if (selectedTab === "trace") {
              primaryTracePendingRef.current = cloneEach(saved.customColumns);
              compareTracePendingRef.current = cloneEach(saved.customColumns);
            } else {
              primarySpansPendingRef.current = cloneEach(saved.customColumns);
              compareSpansPendingRef.current = cloneEach(saved.customColumns);
            }
          }
        } else {
          const traceCols = saved.customColumns.trace || [];
          const spansCols = saved.customColumns.spans || [];
          if (traceCols.length > 0) {
            primaryTracePendingRef.current = cloneEach(traceCols);
            compareTracePendingRef.current = cloneEach(traceCols);
          }
          if (spansCols.length > 0) {
            primarySpansPendingRef.current = cloneEach(spansCols);
            compareSpansPendingRef.current = cloneEach(spansCols);
          }
        }
      }
    } catch {
      /* ignore corrupted localStorage */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayStorageKey]);

  // Load saved filters from localStorage on mount (for default tab)
  useEffect(() => {
    if (activeViewTabId) return; // custom view tabs load from backend
    try {
      const raw = localStorage.getItem(filtersStorageKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.filters?.length > 0) {
        const filtersWithIds = saved.filters.map((f) => ({
          ...f,
          id: f.id || getRandomId(),
        }));
        if (saved.tabType === "spans") {
          setPrimarySpanFilters(filtersWithIds);
        } else {
          setPrimaryTraceFilters(filtersWithIds);
        }
      }
      if (saved.extraFilters?.length > 0) {
        setExtraFiltersRaw(saved.extraFilters);
        setFilterChipsSaved(true);
      }
      if (saved.showCompare) {
        if (saved.compareFilters?.length > 0) {
          const compareFiltersWithIds = saved.compareFilters.map((f) => ({
            ...f,
            id: f.id || getRandomId(),
          }));
          if (saved.tabType === "spans") {
            setCompareSpansFilters(compareFiltersWithIds);
          } else {
            setCompareTraceFilters(compareFiltersWithIds);
          }
        }
        if (saved.compareDateFilter) {
          if (saved.tabType === "spans") {
            setCompareSpansDateFilter(saved.compareDateFilter);
          } else {
            setCompareTraceDateFilter(saved.compareDateFilter);
          }
        }
        if (saved.compareExtraFilters?.length > 0) {
          setCompareExtraFiltersRaw(saved.compareExtraFilters);
        }
      }
    } catch {
      /* ignore corrupted localStorage */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersStorageKey]);

  // Helper: get current custom columns
  const getCustomColumns = useCallback(() => {
    const ck = `${selectedGraph}-${selectedTab === "spans" ? "spans" : "trace"}`;
    return (columns[ck] || []).filter((c) => c.groupBy === "Custom Columns");
  }, [columns, selectedGraph, selectedTab]);

  // Used by the localStorage save so adding customs on one tab doesn't
  // wipe the other's customs (storage key is project-scoped, not tab-scoped).
  const getCustomColumnsByTab = useCallback(() => {
    const traceKey = `${selectedGraph}-trace`;
    const spansKey = `${selectedGraph}-spans`;
    return {
      trace: (columns[traceKey] || []).filter(
        (c) => c.groupBy === "Custom Columns",
      ),
      spans: (columns[spansKey] || []).filter(
        (c) => c.groupBy === "Custom Columns",
      ),
    };
  }, [columns, selectedGraph]);

  const { mutate: updateSavedView } = useUpdateSavedView(observeId);
  const { mutate: createSavedView } = useCreateSavedView(observeId);
  // Workspace-scoped update for user_detail mode — only invoked when isUserMode.
  const { mutate: updateWorkspaceSavedView } =
    useUpdateWorkspaceSavedView(USER_DETAIL_TAB_TYPE);

  const activeViewTabId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const tab = isUserMode ? params.get("userTab") : params.get("tab");
    return tab?.startsWith("view-") ? tab.replace("view-", "") : null;
  }, [activeViewConfig, isUserMode]);

  const buildViewConfig = useCallback(() => {
    // columnState lives inside `display` because the backend serializer
    // whitelists `display` for arbitrary subkeys.
    const activeGridApi =
      selectedTab === "trace"
        ? primaryTraceGridRef.current?.api
        : primarySpanGridRef.current?.api;
    const columnState = activeGridApi?.getColumnState?.() ?? undefined;
    const currentDisplay = {
      viewMode,
      cellHeight,
      showErrors,
      showNonAnnotated,
      showCompare,
      hasEvalFilter,
      customColumns: getCustomColumns(),
      // dateFilter lives inside display for backend-whitelist compatibility.
      dateFilter:
        selectedTab === "trace"
          ? primaryTraceDateFilter
          : primarySpanDateFilter,
      ...(columnState ? { columnState } : {}),
    };
    const mapFilters = (filters) =>
      (filters || []).map((f) => ({
        columnId: f.columnId,
        filterConfig: f.filterConfig,
      }));
    const config = {
      display: currentDisplay,
      filters: mapFilters(
        selectedTab === "trace" ? primaryTraceFilters : primarySpanFilters,
      ),
      extraFilters: extraFilters || [],
    };
    if (showCompare) {
      config.compareFilters = mapFilters(
        selectedTab === "trace" ? compareTraceFilters : compareSpansFilters,
      );
      config.compareDateFilter =
        selectedTab === "trace"
          ? compareTraceDateFilter
          : compareSpansDateFilter;
      config.compareExtraFilters = compareExtraFilters || [];
    }
    return config;
  }, [
    viewMode,
    cellHeight,
    showErrors,
    showNonAnnotated,
    showCompare,
    hasEvalFilter,
    getCustomColumns,
    selectedTab,
    primaryTraceFilters,
    primarySpanFilters,
    primaryTraceDateFilter,
    primarySpanDateFilter,
    compareTraceFilters,
    compareSpansFilters,
    compareTraceDateFilter,
    compareSpansDateFilter,
    extraFilters,
    compareExtraFilters,
  ]);

  useEffect(() => {
    registerGetViewConfig(buildViewConfig);
    return () => registerGetViewConfig(null);
  }, [registerGetViewConfig, buildViewConfig]);

  useEffect(() => {
    const getTabType = () => (selectedTab === "spans" ? "spans" : "traces");
    registerGetTabType(getTabType);
    return () => registerGetTabType(null);
  }, [registerGetTabType, selectedTab]);

  // Bound to ObserveToolbar's Save view button.
  const handleSaveView = useCallback(() => {
    if (!activeViewTabId) return;
    const config = buildViewConfig();
    const mutate = isUserMode ? updateWorkspaceSavedView : updateSavedView;
    mutate(
      { id: activeViewTabId, config },
      {
        onSuccess: (response) => {
          setActiveViewConfig(response?.data?.result?.config ?? config);
          enqueueSnackbar("View updated", { variant: "success" });
        },
        onError: () =>
          enqueueSnackbar("Failed to update view", { variant: "error" }),
      },
    );
  }, [
    activeViewTabId,
    buildViewConfig,
    isUserMode,
    updateSavedView,
    updateWorkspaceSavedView,
    setActiveViewConfig,
  ]);

  // Default tab only — saved views go through handleSaveView instead.
  useEffect(() => {
    if (activeViewTabId) return;
    const currentDisplay = {
      viewMode,
      cellHeight,
      showErrors,
      showNonAnnotated,
      showCompare,
      hasEvalFilter,
      // Keyed by tab type so adding a custom on spans doesn't overwrite
      // traces' customs (storage key is project-scoped, not tab-scoped).
      customColumns: getCustomColumnsByTab(),
    };
    try {
      localStorage.setItem(displayStorageKey, JSON.stringify(currentDisplay));
    } catch {
      /* quota exceeded */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    viewMode,
    cellHeight,
    showErrors,
    showNonAnnotated,
    showCompare,
    hasEvalFilter,
    displayStorageKey,
    getCustomColumnsByTab,
  ]);

  const handleAddEvals = useCallback(() => {
    const isTrace = selectedTab === "trace";
    const returnTo =
      typeof window !== "undefined"
        ? `${window.location.pathname}${window.location.search}`
        : undefined;
    const url = buildAddEvalsDraft({
      observeId,
      rowType: isTrace ? "traces" : "spans",
      mainFilters: isTrace ? primaryTraceFilters : primarySpanFilters,
      extraFilters,
      dateFilter: isTrace ? primaryTraceDateFilter : primarySpanDateFilter,
      returnTo,
    });
    navigate(url);
  }, [
    selectedTab,
    observeId,
    primaryTraceFilters,
    primarySpanFilters,
    primaryTraceDateFilter,
    primarySpanDateFilter,
    extraFilters,
    navigate,
  ]);

  const handleResetView = useCallback(() => {
    setViewMode(DEFAULT_DISPLAY_CONFIG.viewMode);
    setHasEvalFilter(DEFAULT_DISPLAY_CONFIG.hasEvalFilter);
    setShowCompare(DEFAULT_DISPLAY_CONFIG.showCompare);
    setCellHeight(DEFAULT_DISPLAY_CONFIG.cellHeight);
    setShowErrors(DEFAULT_DISPLAY_CONFIG.showErrors);
    setShowNonAnnotated(DEFAULT_DISPLAY_CONFIG.showNonAnnotated);
    // Remove custom columns
    const ck = `${selectedGraph}-${selectedTab === "spans" ? "spans" : "trace"}`;
    setColumns((prev) => ({
      ...prev,
      [ck]: (prev[ck] || []).filter((c) => c.groupBy !== "Custom Columns"),
    }));
    try {
      localStorage.removeItem(displayStorageKey);
    } catch {
      /* noop */
    }
    try {
      localStorage.removeItem(filtersStorageKey);
    } catch {
      /* noop */
    }
    enqueueSnackbar("View reset to defaults", { variant: "info" });
  }, [selectedGraph, selectedTab, displayStorageKey, filtersStorageKey]);

  const handleSetDefaultView = useCallback(() => {
    const configPayload = buildViewConfig();

    if (activeViewTabId) {
      updateSavedView(
        { id: activeViewTabId, visibility: "project", config: configPayload },
        {
          onSuccess: () =>
            enqueueSnackbar("View set as default for everyone", {
              variant: "success",
            }),
        },
      );
    } else {
      createSavedView(
        {
          project_id: observeId,
          name: "Default View",
          tab_type: selectedTab === "trace" ? "traces" : "spans",
          visibility: "project",
          config: configPayload,
        },
        {
          onSuccess: () =>
            enqueueSnackbar("View set as default for everyone", {
              variant: "success",
            }),
        },
      );
    }
  }, [
    activeViewTabId,
    selectedTab,
    observeId,
    buildViewConfig,
    updateSavedView,
    createSavedView,
  ]);

  // Eval filter chips — drives the Filter button's red "active" dot.
  // Keep this scoped to extraFilters only; date/column changes should NOT
  // light up the Filter button (those have their own affordances).
  const hasActiveFilter = useMemo(
    () => extraFilters?.length > 0,
    [extraFilters],
  );

  // "Save view" button is a convenience affordance for a custom saved view
  // that has been modified. On a default tab, the "+" button alone handles
  // save-as-new — we don't want Save view cluttering the toolbar there.
  const canSaveView = useMemo(() => {
    if (!activeViewConfig) return false;

    const baselineDisplay = activeViewConfig.display || {};
    const baselineExtraFilters = activeViewConfig.extraFilters || [];
    const baselineDateOption = baselineDisplay.dateFilter?.dateOption ?? null;
    const baselineColumnFilters = activeViewConfig.filters || [];

    if (!filtersContentEqual(extraFilters, baselineExtraFilters)) return true;

    const currentDate =
      selectedTab === "trace" ? primaryTraceDateFilter : primarySpanDateFilter;
    if ((currentDate?.dateOption ?? null) !== baselineDateOption) return true;

    const columnFilters =
      selectedTab === "trace" ? primaryTraceFilters : primarySpanFilters;
    if (!filtersContentEqual(columnFilters, baselineColumnFilters)) return true;

    if (
      baselineDisplay.viewMode !== undefined &&
      baselineDisplay.viewMode !== viewMode
    ) {
      return true;
    }
    if (
      baselineDisplay.cellHeight !== undefined &&
      baselineDisplay.cellHeight !== cellHeight
    ) {
      return true;
    }
    if (
      baselineDisplay.showErrors !== undefined &&
      baselineDisplay.showErrors !== showErrors
    ) {
      return true;
    }
    if (
      baselineDisplay.showNonAnnotated !== undefined &&
      baselineDisplay.showNonAnnotated !== showNonAnnotated
    ) {
      return true;
    }
    if (
      baselineDisplay.showCompare !== undefined &&
      baselineDisplay.showCompare !== showCompare
    ) {
      return true;
    }
    if (
      baselineDisplay.hasEvalFilter !== undefined &&
      baselineDisplay.hasEvalFilter !== hasEvalFilter
    ) {
      return true;
    }
    // Custom columns: did the user add/remove a custom column since the
    // saved view? Compare by id, not deep shape.
    const baselineCustom = Array.isArray(baselineDisplay.customColumns)
      ? baselineDisplay.customColumns
      : [];
    const currentCustom = getCustomColumns() || [];
    if (currentCustom.length !== baselineCustom.length) return true;
    if (currentCustom.length > 0) {
      const baselineIds = new Set(baselineCustom.map((c) => c?.id));
      for (const col of currentCustom) {
        if (!baselineIds.has(col?.id)) return true;
      }
    }
    return false;
  }, [
    activeViewConfig,
    extraFilters,
    selectedTab,
    primaryTraceDateFilter,
    primarySpanDateFilter,
    primaryTraceFilters,
    primarySpanFilters,
    viewMode,
    cellHeight,
    showErrors,
    showNonAnnotated,
    showCompare,
    hasEvalFilter,
    getCustomColumns,
  ]);

  // Defer the visibility signal so it catches up with activeViewConfig
  // (which updates inside startTransition). Without this, canSaveView briefly
  // returns true on view-switch because filter state updates urgently while
  // the baseline update trails by a render, which makes the button flicker.
  const canSaveViewDeferred = useDeferredValue(canSaveView);

  const currentGridRef = useMemo(() => {
    if (selectedGraph === "primary" && selectedTab === "trace") {
      return primaryTraceGridRef;
    }
    if (selectedGraph === "primary" && selectedTab === "spans") {
      return primarySpanGridRef;
    }
    if (selectedGraph === "compare" && selectedTab === "trace") {
      return compareTraceGridRef;
    }
    if (selectedGraph === "compare" && selectedTab === "spans") {
      return compareSpanGridRef;
    }
    return null;
  }, [selectedGraph, selectedTab]);

  return (
    <Box sx={{ paddingX: theme.spacing(2) }}>
      <Helmet>
        <title>Observe - LLM Tracing</title>
      </Helmet>
      {/* Old date/filter/compare controls — hidden, replaced by ObserveToolbar */}
      <Box
        sx={{
          paddingY: theme.spacing(2),
          display: "none",
          flexDirection: "column",
          gap: theme.spacing(2.5),
          position: "sticky",
          zIndex: 100,
          top: 0,
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(2),
          }}
        >
          {/* Normal Mode */}
          {!showCompare ? (
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              gap={theme.spacing(2)}
            >
              {/* Left Side - Primary Controls */}
              <Box display="flex" alignItems="center">
                <FilterErrorBoundary>
                  <TracingControls
                    dateFilter={
                      selectedTab === "trace"
                        ? primaryTraceDateFilter
                        : primarySpanDateFilter
                    }
                    setDateFilter={
                      selectedTab === "trace"
                        ? setPrimaryTraceDateFilter
                        : setPrimarySpanDateFilter
                    }
                    observeId={observeId}
                  />
                </FilterErrorBoundary>

                {/* Refresh */}
                {/* <Button
                  onClick={primaryRefreshFn}
                  sx={{
                    minWidth: "fit-content",
                    whiteSpace: "nowrap",
                  }}
                >
                  <Box
                    sx={{
                      width: 20,
                      height: 20,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      mr: 1, // spacing between icon and text
                    }}
                  >
                    {loading && latestActive ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify width={18} icon="clarity:refresh-line" />
                    )}
                  </Box>

                  <Typography typography="s1" fontWeight="fontWeightRegular">
                    Refresh
                  </Typography>
                </Button> */}
              </Box>
              {/* Filter Button */}
              <Box sx={{ display: "flex", alignItems: "center", gap: "12px" }}>
                {projectSource === PROJECT_SOURCE.SIMULATOR && (
                  <LoadingButton
                    color={selectedCallIds.length > 0 ? "primary" : "inherit"}
                    loading={isCreatingReplaySessions}
                    startIcon={
                      <SvgColor
                        src="/assets/icons/navbar/ic_get_started.svg"
                        sx={{ height: 16, width: 16 }}
                      />
                    }
                    onClick={() => {
                      if (openReplaySessionDrawer[REPLAY_MODULES.TRACES]) {
                        setIsReplayDrawerCollapsed(
                          REPLAY_MODULES.TRACES,
                          false,
                        );
                        return;
                      }
                      createReplaySessions(
                        {
                          project_id: observeId,
                          replay_type: REPLAY_MODULES.TRACES,
                          ids: selectedCallIds,
                          select_all: false,
                        },
                        {
                          onSuccess: (data) => {
                            useSessionsGridStore
                              .getState()
                              .setToggledNodes(selectedCallIds);
                            setCreatedReplay(data?.data?.result);
                            setReplayType(REPLAY_TYPES.NEW_GROUP);
                            setOpenReplaySessionDrawer(
                              REPLAY_MODULES.TRACES,
                              true,
                            );
                          },
                        },
                      );
                    }}
                    size="small"
                    variant="outlined"
                    disabled={
                      selectedCallIds.length === 0 ||
                      Object.values(openReplaySessionDrawer).some(
                        (open) => open,
                      )
                    }
                  >
                    {`Replay Call${selectedCallIds.length !== 1 ? "s" : ""} (${selectedCallIds.length})`}
                  </LoadingButton>
                )}
                <Button
                  sx={{
                    paddingX: theme.spacing(2),
                    minWidth: "fit-content",
                    ml: 1,
                    whiteSpace: "nowrap",
                  }}
                  variant="outlined"
                  size="small"
                  startIcon={
                    hasActiveFilter ? (
                      <Badge variant="dot" color="error" overlap="circular">
                        <SvgColor
                          src="/assets/icons/components/ic_newfilter.svg"
                          sx={{
                            color: "text.primary",
                            width: "20px",
                            height: "20px",
                          }}
                        />
                      </Badge>
                    ) : (
                      <SvgColor
                        src="/assets/icons/components/ic_newfilter.svg"
                        sx={{
                          color: "text.primary",
                          height: "20px",
                          width: "20px",
                        }}
                      />
                    )
                  }
                  onClick={() => setIsPrimaryFilterOpen(!isPrimaryFilterOpen)}
                >
                  <Typography typography="s2_1" fontWeight={"fontWeightMedium"}>
                    {isPrimaryFilterOpen ? "Hide filter" : "Add filter"}
                  </Typography>
                </Button>

                <CustomTooltip
                  show={true}
                  title="To compare two metrics or the same metric across different time ranges"
                  placement="bottom"
                  arrow
                  size="small"
                  type="black"
                  slotProps={{
                    tooltip: {
                      sx: {
                        maxWidth: "200px !important",
                      },
                    },
                    popper: {
                      modifiers: {
                        name: "preventOverflow",
                        options: {
                          boundary: "viewport",
                          padding: 12,
                        },
                      },
                    },
                  }}
                >
                  <Button
                    variant="outlined"
                    color="primary"
                    size="small"
                    onClick={() => {
                      setShowCompare(true);
                      setAutoSizeAllCols(false);
                      trackEvent(Events.pObserveCompareRunClicked, {
                        [PropertyName.id]: observeId,
                      });
                    }}
                    startIcon={<Iconify icon="iconamoon:compare" />}
                  >
                    Compare
                  </Button>
                </CustomTooltip>
              </Box>
            </Box>
          ) : (
            <Box display="flex" justifyContent="space-between">
              <Box display="flex" alignItems="center" gap="12px">
                <Box display="flex" alignItems="center" gap="12px" flex={1}>
                  <Box
                    sx={() => {
                      const { tagBackground: bg, tagForeground: text } =
                        getUniqueColorPalette(1);
                      return {
                        width: theme.spacing(3),
                        height: theme.spacing(3.125),
                        borderRadius: theme.spacing(0.5),
                        backgroundColor: bg,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 600,
                        color: text,
                      };
                    }}
                  >
                    A
                  </Box>
                  <FilterErrorBoundary>
                    <TracingControls
                      dateFilter={
                        selectedTab === "trace"
                          ? primaryTraceDateFilter
                          : primarySpanDateFilter
                      }
                      setDateFilter={
                        selectedTab === "trace"
                          ? setPrimaryTraceDateFilter
                          : setPrimarySpanDateFilter
                      }
                      observeId={observeId}
                    />
                  </FilterErrorBoundary>
                </Box>
                <Typography typography="s2" color="text.primary">
                  vs
                </Typography>
                <Box display="flex" alignItems="center" gap="12px" flex={1}>
                  <Box
                    sx={() => {
                      const { tagBackground: bg, tagForeground: text } =
                        getUniqueColorPalette(3);
                      return {
                        width: theme.spacing(3),
                        height: theme.spacing(3.125),
                        borderRadius: theme.spacing(0.5),
                        backgroundColor: bg,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 600,
                        color: text,
                      };
                    }}
                  >
                    B
                  </Box>

                  <FilterErrorBoundary>
                    <TracingControls
                      dateFilter={
                        selectedTab === "trace"
                          ? compareTraceDateFilter
                          : compareSpansDateFilter
                      }
                      setDateFilter={
                        selectedTab === "trace"
                          ? setCompareTraceDateFilter
                          : setCompareSpansDateFilter
                      }
                      observeId={observeId}
                    />
                  </FilterErrorBoundary>
                </Box>
              </Box>
              <Box display="flex" alignItems="center" gap={theme.spacing(2)}>
                <ObserveIconButton
                  onClick={() => setIsPrimaryFilterOpen(!isPrimaryFilterOpen)}
                  size="small"
                >
                  {hasActiveFilter ? (
                    <Badge variant="dot" color="error">
                      <SvgColor
                        src="/assets/icons/components/ic_newfilter.svg"
                        sx={{
                          color: "text.primary",
                          width: "20px",
                          height: "20px",
                        }}
                      />
                    </Badge>
                  ) : (
                    <SvgColor
                      src="/assets/icons/components/ic_newfilter.svg"
                      sx={{
                        color: "text.primary",
                        width: "20px",
                        height: "20px",
                      }}
                    />
                  )}
                </ObserveIconButton>

                {/* <ObserveIconButton
                  onClick={primaryRefreshFn}
                  size="small"
                >
                  {loading && latestActive ? (
                    <CircularProgress size={16} />
                  ) : (
                    <Iconify icon="clarity:refresh-line" />
                  )}
                </ObserveIconButton> */}

                <ObserveIconButton
                  onClick={() => {
                    setShowCompare(false);
                    setAutoSizeAllCols(false);
                  }}
                  size="small"
                >
                  <Iconify
                    icon="mingcute:close-line"
                    sx={{ color: "text.disabled" }}
                  />
                </ObserveIconButton>
              </Box>
            </Box>
          )}
        </Box>
      </Box>
      {/* Active filter chips — in compare mode, only show Clear/Save at top (chips are inline on each graph) */}
      {!filterChipsSaved && !showCompare && (
        <FilterChips
          extraFilters={extraFilters.map((f) => ({
            ...f,
            display_name:
              f.display_name ||
              primaryFilterDefinition?.find((c) => c.propertyId === f.column_id)
                ?.propertyName,
          }))}
          fieldLabelMap={filterChipLabelMap}
          onRemoveFilter={(idx) => {
            // Chips are keyed by array index, so any removal re-mounts the
            // later chips and invalidates a chip-anchored popover ref.
            // Clear it so the popover falls back to the toolbar button anchor.
            setExternalFilterAnchor(null);
            setExtraFilters((prev) => prev.filter((_, i) => i !== idx));
          }}
          onClearAll={() => {
            setExternalFilterAnchor(null);
            setExtraFilters([]);
            try {
              localStorage.removeItem(filtersStorageKey);
            } catch {
              /* noop */
            }
          }}
          onAddFilter={(anchorEl) => {
            setFilterTarget("primary");
            setExternalFilterAnchor(anchorEl || null);
            setIsPrimaryFilterOpen(true);
          }}
          onChipClick={(_idx, anchorEl) => {
            setFilterTarget("primary");
            setExternalFilterAnchor(anchorEl || null);
            setIsPrimaryFilterOpen(true);
          }}
          onSave={() => {
            setFilterChipsSaved(true);
            if (activeViewTabId) {
              updateSavedView({
                id: activeViewTabId,
                config: buildViewConfig(),
              });
            } else {
              try {
                const mapFilters = (filters) =>
                  (filters || []).map((f) => ({
                    columnId: f.columnId,
                    filterConfig: f.filterConfig,
                  }));
                localStorage.setItem(
                  filtersStorageKey,
                  JSON.stringify({
                    tabType: selectedTab,
                    filters: mapFilters(
                      selectedTab === "trace"
                        ? primaryTraceFilters
                        : primarySpanFilters,
                    ),
                    extraFilters: extraFilters || [],
                  }),
                );
              } catch {
                /* quota exceeded */
              }
            }
            enqueueSnackbar("Filters saved", { variant: "success" });
          }}
        />
      )}
      {/* Compare mode: show Clear/Save buttons at top when any graph has filters */}
      {showCompare &&
        (extraFilters?.length > 0 || compareExtraFilters?.length > 0) && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              gap: 1,
              px: 2,
              py: 0.5,
            }}
          >
            <Button
              size="small"
              onClick={() => {
                setExtraFilters([]);
                setCompareExtraFilters([]);
                try {
                  localStorage.removeItem(filtersStorageKey);
                } catch {
                  /* noop */
                }
              }}
              sx={{
                textTransform: "none",
                fontSize: 12,
                color: "text.secondary",
                minWidth: "auto",
                p: 0,
                "&:hover": { color: "text.primary", bgcolor: "transparent" },
              }}
            >
              Clear
            </Button>
            <Button
              size="small"
              onClick={() => {
                setFilterChipsSaved(true);
                if (activeViewTabId) {
                  updateSavedView({
                    id: activeViewTabId,
                    config: buildViewConfig(),
                  });
                } else {
                  try {
                    const mapFilters = (filters) =>
                      (filters || []).map((f) => ({
                        columnId: f.columnId,
                        filterConfig: f.filterConfig,
                      }));
                    localStorage.setItem(
                      filtersStorageKey,
                      JSON.stringify({
                        tabType: selectedTab,
                        showCompare: true,
                        filters: mapFilters(
                          selectedTab === "trace"
                            ? primaryTraceFilters
                            : primarySpanFilters,
                        ),
                        compareFilters: mapFilters(
                          selectedTab === "trace"
                            ? compareTraceFilters
                            : compareSpansFilters,
                        ),
                        compareDateFilter:
                          selectedTab === "trace"
                            ? compareTraceDateFilter
                            : compareSpansDateFilter,
                        extraFilters: extraFilters || [],
                        compareExtraFilters: compareExtraFilters || [],
                      }),
                    );
                  } catch {
                    /* quota exceeded */
                  }
                }
                enqueueSnackbar("Filters saved", { variant: "success" });
              }}
              sx={{
                textTransform: "none",
                fontSize: 12,
                fontWeight: 600,
                color: "#573FCC",
                minWidth: "auto",
                p: 0,
                "&:hover": { bgcolor: "transparent" },
              }}
            >
              Save
            </Button>
          </Box>
        )}
      <Box
        sx={{
          display: "flex",
          gap: theme.spacing(2),
          flexDirection: "column",
          overflow: "hidden",
          position: "relative",
        }}
        ref={graphBoxRef}
      >
        {/* Graph section — lazy loaded */}
        <Suspense
          fallback={
            <Box
              sx={{
                height: 120,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <CircularProgress size={20} />
            </Box>
          }
        >
          {/* Primary Graph — dual-axis bars + line. Hidden in user mode
              (PrimaryGraph requires observeId for its query). */}
          {viewMode === "graph" && !isUserMode && (
            <>
              <PrimaryGraph
                filters={
                  selectedTab === "trace"
                    ? primaryTraceValidatedFilters
                    : primarySpanValidatedFilters
                }
                dateFilter={
                  selectedTab === "trace"
                    ? primaryTraceDateFilter
                    : primarySpanDateFilter
                }
                setDateFilter={
                  selectedTab === "trace"
                    ? setPrimaryTraceDateFilter
                    : setPrimarySpanDateFilter
                }
                selectedInterval={selectedPrimaryInterval}
                hasEvalFilter={hasEvalFilter}
                showDateFilter={showCompare}
                observeIdOverride={observeId}
                onFilterToggle={
                  showCompare
                    ? (e) => handleCompareFilterToggle(e, "primary")
                    : undefined
                }
                hasActiveFilter={showCompare && extraFilters?.length > 0}
                trafficLabel={selectedTab === "spans" ? "spans" : "traces"}
              />
              {showCompare && (
                <PrimaryGraph
                  filters={
                    selectedTab === "trace"
                      ? compareTraceValidatedFilters
                      : compareSpansValidatedFilters
                  }
                  dateFilter={
                    selectedTab === "trace"
                      ? compareTraceDateFilter
                      : compareSpansDateFilter
                  }
                  setDateFilter={
                    selectedTab === "trace"
                      ? setCompareTraceDateFilter
                      : setCompareSpansDateFilter
                  }
                  selectedInterval={selectedSecondaryInterval}
                  hasEvalFilter={hasEvalFilter}
                  graphLabel="Compare Graph"
                  showDateFilter
                  observeIdOverride={observeId}
                  onFilterToggle={(e) =>
                    handleCompareFilterToggle(e, "compare")
                  }
                  hasActiveFilter={compareExtraFilters?.length > 0}
                  lineColorOverride={
                    theme.palette.mode === "dark"
                      ? "rgba(245, 166, 147, 0.85)"
                      : "rgba(230, 120, 100, 0.70)"
                  }
                  barColorOverride={
                    theme.palette.mode === "dark"
                      ? "rgba(220, 160, 130, 0.30)"
                      : "rgba(230, 170, 147, 0.25)"
                  }
                  trafficLabel={selectedTab === "spans" ? "spans" : "traces"}
                />
              )}
            </>
          )}

          {/* Agent Graph — DAG visualization */}
          {viewMode === "agentGraph" && (
            <>
              <Box sx={{ mx: 2, my: 1 }}>
                {showCompare && (
                  <CompareGraphHeader
                    compareType="primary"
                    dateFilter={
                      selectedTab === "trace"
                        ? primaryTraceDateFilter
                        : primarySpanDateFilter
                    }
                    setDateFilter={
                      selectedTab === "trace"
                        ? setPrimaryTraceDateFilter
                        : setPrimarySpanDateFilter
                    }
                    onFilterToggle={(e) =>
                      handleCompareFilterToggle(e, "primary")
                    }
                    hasActiveFilter={extraFilters?.length > 0}
                    extraFilters={extraFilters}
                    fieldLabelMap={filterChipLabelMap}
                    onRemoveFilter={(idx) =>
                      setExtraFilters((prev) =>
                        prev.filter((_, i) => i !== idx),
                      )
                    }
                    onClearFilters={() => setExtraFilters([])}
                  />
                )}
                <Box sx={{ height: 220 }}>
                  <AgentGraph
                    data={agentGraphData}
                    isLoading={isAgentGraphLoading}
                    isError={isAgentGraphError}
                    onNodeClick={handleAgentNodeClick}
                  />
                </Box>
              </Box>
              {showCompare && (
                <Box sx={{ mx: 2, my: 1 }}>
                  <CompareGraphHeader
                    compareType="compare"
                    dateFilter={
                      selectedTab === "trace"
                        ? compareTraceDateFilter
                        : compareSpansDateFilter
                    }
                    setDateFilter={
                      selectedTab === "trace"
                        ? setCompareTraceDateFilter
                        : setCompareSpansDateFilter
                    }
                    onFilterToggle={(e) =>
                      handleCompareFilterToggle(e, "compare")
                    }
                    hasActiveFilter={compareExtraFilters?.length > 0}
                    extraFilters={compareExtraFilters}
                    fieldLabelMap={filterChipLabelMap}
                    onRemoveFilter={(idx) =>
                      setCompareExtraFilters((prev) =>
                        prev.filter((_, i) => i !== idx),
                      )
                    }
                    onClearFilters={() => setCompareExtraFilters([])}
                  />
                  <Box sx={{ height: 220 }}>
                    <AgentGraph
                      data={compareAgentGraphData}
                      isLoading={isCompareAgentGraphLoading}
                      isError={isCompareAgentGraphError}
                      onNodeClick={handleAgentNodeClick}
                    />
                  </Box>
                </Box>
              )}
            </>
          )}

          {/* Agent Path — sequential flow */}
          {viewMode === "agentPath" && (
            <>
              <Box>
                {showCompare && (
                  <Box sx={{ mx: 2, mt: 1 }}>
                    <CompareGraphHeader
                      compareType="primary"
                      dateFilter={
                        selectedTab === "trace"
                          ? primaryTraceDateFilter
                          : primarySpanDateFilter
                      }
                      setDateFilter={
                        selectedTab === "trace"
                          ? setPrimaryTraceDateFilter
                          : setPrimarySpanDateFilter
                      }
                      onFilterToggle={(e) =>
                        handleCompareFilterToggle(e, "primary")
                      }
                      hasActiveFilter={extraFilters?.length > 0}
                      extraFilters={extraFilters}
                      fieldLabelMap={filterChipLabelMap}
                      onRemoveFilter={(idx) =>
                        setExtraFilters((prev) =>
                          prev.filter((_, i) => i !== idx),
                        )
                      }
                      onClearFilters={() => setExtraFilters([])}
                    />
                  </Box>
                )}
                <AgentPath
                  data={agentGraphData}
                  isLoading={isAgentGraphLoading}
                  isError={isAgentGraphError}
                  onNodeClick={handleAgentNodeClick}
                />
              </Box>
              {showCompare && (
                <Box>
                  <Box sx={{ mx: 2, mt: 1 }}>
                    <CompareGraphHeader
                      compareType="compare"
                      dateFilter={
                        selectedTab === "trace"
                          ? compareTraceDateFilter
                          : compareSpansDateFilter
                      }
                      setDateFilter={
                        selectedTab === "trace"
                          ? setCompareTraceDateFilter
                          : setCompareSpansDateFilter
                      }
                      onFilterToggle={(e) =>
                        handleCompareFilterToggle(e, "compare")
                      }
                      hasActiveFilter={compareExtraFilters?.length > 0}
                      extraFilters={compareExtraFilters}
                      fieldLabelMap={filterChipLabelMap}
                      onRemoveFilter={(idx) =>
                        setCompareExtraFilters((prev) =>
                          prev.filter((_, i) => i !== idx),
                        )
                      }
                      onClearFilters={() => setCompareExtraFilters([])}
                    />
                  </Box>
                  <AgentPath
                    data={compareAgentGraphData}
                    isLoading={isCompareAgentGraphLoading}
                    isError={isCompareAgentGraphError}
                    onNodeClick={handleAgentNodeClick}
                  />
                </Box>
              )}
            </>
          )}
        </Suspense>
      </Box>

      <Box sx={{ paddingY: theme.spacing(1) }}>
        <Box>
          <ShowComponent condition={showCompare}>
            <Box
              sx={{
                borderBottom: 1,
                borderColor: "divider",
                pl: theme.spacing(2.5),
              }}
            >
              <Tabs
                value={selectedGraph}
                onChange={(e, value) => {
                  setAutoSizeAllCols(false);
                  setSelectedGraph(value);
                  resetSpanGridStore();
                  resetTraceGridStore();
                }}
                aria-label="changes tabs"
                textColor="primary"
                TabIndicatorProps={{
                  style: {
                    backgroundColor: theme.palette.primary.main,
                  },
                }}
                sx={{
                  minHeight: 0,
                  "& .MuiTab-root": {
                    margin: "0 !important",
                    fontWeight: "600",
                    color: "primary.main",
                    "&:not(.Mui-selected)": {
                      color: "text.disabled",
                      fontWeight: "500",
                    },
                  },
                }}
              >
                <Tab
                  sx={{
                    margin: theme.spacing(0),
                    px: theme.spacing(1.875),
                  }}
                  label="Primary Graph"
                  value="primary"
                />
                <Tab
                  sx={{
                    margin: theme.spacing(0),
                    px: theme.spacing(1.875),
                  }}
                  label="Comparison Graph"
                  value="compare"
                />
              </Tabs>
            </Box>
          </ShowComponent>
          <>
            {/* Toolbar */}
            <ObserveToolbar
              dateLabel={dateLabel}
              dateFilter={
                selectedTab === "trace"
                  ? primaryTraceDateFilter
                  : primarySpanDateFilter
              }
              setDateFilter={
                selectedTab === "trace"
                  ? setPrimaryTraceDateFilter
                  : setPrimarySpanDateFilter
              }
              hasActiveFilter={hasActiveFilter}
              canSaveView={canSaveViewDeferred}
              onSaveView={handleSaveView}
              onFilterToggle={() => {
                // Clear any chip/+ anchor so the popover re-anchors to the
                // toolbar Filter button (avoids opening on a stale anchor).
                setExternalFilterAnchor(null);
                setIsPrimaryFilterOpen(!isPrimaryFilterOpen);
              }}
              onApplyExtraFilters={setExtraFilters}
              graphFilters={extraFilters}
              isFilterOpen={isPrimaryFilterOpen}
              externalFilterAnchor={externalFilterAnchor}
              filterTarget={filterTarget}
              onApplyCompareExtraFilters={setCompareExtraFilters}
              filters={
                selectedTab === "trace"
                  ? primaryTraceFilters
                  : primarySpanFilters
              }
              setFilters={
                selectedTab === "trace"
                  ? setPrimaryTraceFilters
                  : setPrimarySpanFilters
              }
              filterDefinition={primaryFilterDefinition}
              filterFields={toolbarFilterFields}
              tab={selectedTab}
              defaultFilter={defaultFilterBase}
              columns={columns[columnKey]}
              onColumnVisibilityChange={(e) => {
                setColumnConfigureAnchor(e?.currentTarget || null);
                setOpenColumnConfigure(true);
              }}
              setColumns={(updatedColumns) =>
                setColumns((prev) => ({
                  ...prev,
                  [columnKey]: updatedColumns,
                }))
              }
              onAutoSize={handleAutoSize}
              autoSizeAllCols={autoSizeAllCols}
              onAddCustomColumn={() => setOpenCustomColumn(true)}
              cellHeight={cellHeight}
              setCellHeight={setCellHeight}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              hasEvalFilter={hasEvalFilter}
              onToggleEvalFilter={() => setHasEvalFilter(!hasEvalFilter)}
              showEvalToggle={
                !!columnKey &&
                columns[columnKey]?.some(
                  (col) => col?.groupBy === "Evaluation Metrics",
                )
              }
              showErrors={showErrors}
              onToggleErrors={() => setShowErrors(!showErrors)}
              showNonAnnotated={showNonAnnotated}
              onToggleNonAnnotated={() =>
                setShowNonAnnotated(!showNonAnnotated)
              }
              groupBy={groupBy}
              onGroupByChange={handleGroupByChange}
              hiddenGroupByOptions={hiddenGroupByOptions}
              rowCount={currentGridRef.current?.api?.totalRowCount}
              onCompareToggle={() => setShowCompare(!showCompare)}
              isCompareActive={showCompare}
              onResetView={handleResetView}
              onSetDefaultView={handleSetDefaultView}
              projectId={observeId}
              bulkActions={(() => {
                if (projectSource === PROJECT_SOURCE.SIMULATOR) {
                  return [
                    {
                      id: "replay",
                      label: "Replay Calls",
                      icon: "mdi:play-outline",
                    },
                    {
                      id: "dataset",
                      label: "Move to dataset",
                      icon: "mdi:folder-move-outline",
                    },
                    {
                      id: "tags",
                      label: "Add tags",
                      icon: "mdi:tag-outline",
                    },
                    {
                      id: "annotation-queue",
                      label: "Add to annotation queue",
                      icon: "mdi:clipboard-list-outline",
                    },
                  ];
                }
                const all = [
                  {
                    id: "dataset",
                    label: "Move to dataset",
                    icon: "mdi:folder-move-outline",
                  },
                  {
                    id: "tags",
                    label: "Add tags",
                    icon: "mdi:tag-outline",
                  },
                  {
                    id: "annotation-queue",
                    label: "Add to annotation queue",
                    icon: "mdi:clipboard-list-outline",
                  },
                  {
                    id: "annotate",
                    label: "Annotate",
                    icon: "mdi:pencil-box-outline",
                    requiresSingle: true,
                  },
                ];
                // Annotate requires a span — hide on trace tab
                return selectedTab === "trace"
                  ? all.filter((a) => a.id !== "annotate")
                  : all;
              })()}
              selectedCount={
                projectSource === PROJECT_SOURCE.SIMULATOR
                  ? simCallFilterSelectionMode
                    ? simCallMeta.totalMatching ??
                      simCallMeta.totalPages * simCallMeta.pageLimit
                    : selectedCallIds?.length || 0
                  : selectedTab === "trace"
                    ? allTracesSelected
                      ? Math.max(
                          (totalTraces || 0) - (selectedTraces?.length || 0),
                          1,
                        )
                      : selectedTraces?.length || 0
                    : allSpansSelected
                      ? Math.max(
                          (totalSpans || 0) - (selectedSpans?.length || 0),
                          1,
                        )
                      : selectedSpans?.length || 0
              }
              allMatching={
                (projectSource === PROJECT_SOURCE.SIMULATOR &&
                  simCallFilterSelectionMode) ||
                (selectedTab === "trace" && filterSelectionMode) ||
                (selectedTab === "spans" && spanFilterSelectionMode)
              }
              onClearSelection={() => {
                if (projectSource === PROJECT_SOURCE.SIMULATOR) {
                  primaryCallLogsGridRef.current?.deselectAll?.();
                  compareCallLogsGridRef.current?.deselectAll?.();
                  setSelectedCallIds([]);
                  return;
                }
                if (selectedTab === "trace") {
                  primaryTraceGridRef.current?.api?.deselectAll();
                  if (selectedGraph === "compare") {
                    compareTraceGridRef.current?.api?.deselectAll();
                  }
                } else {
                  primarySpanGridRef.current?.api?.deselectAll();
                  if (selectedGraph === "compare") {
                    compareSpanGridRef.current?.api?.deselectAll();
                  }
                }
              }}
              onBulkAction={(actionId, event) => {
                const anchor = event?.currentTarget || null;
                const isSimulator = projectSource === PROJECT_SOURCE.SIMULATOR;
                // Voice/calls path: route only its own actions.
                if (isSimulator) {
                  if (actionId === "annotation-queue") {
                    setQueueAnchorEl(anchor);
                  } else if (actionId === "dataset") {
                    setOpenAddDataset(true);
                  } else if (actionId === "replay") {
                    // Mirror the standalone "Replay Calls" button flow
                    if (openReplaySessionDrawer[REPLAY_MODULES.TRACES]) {
                      setIsReplayDrawerCollapsed(REPLAY_MODULES.TRACES, false);
                      return;
                    }
                    createReplaySessions(
                      {
                        project_id: observeId,
                        replay_type: REPLAY_MODULES.TRACES,
                        ids: selectedCallIds,
                        select_all: false,
                      },
                      {
                        onSuccess: (data) => {
                          useSessionsGridStore
                            .getState()
                            .setToggledNodes(selectedCallIds);
                          setCreatedReplay(data?.data?.result);
                          setReplayType(REPLAY_TYPES.NEW_GROUP);
                          setOpenReplaySessionDrawer(
                            REPLAY_MODULES.TRACES,
                            true,
                          );
                        },
                        onError: () => {
                          enqueueSnackbar("Failed to start replay", {
                            variant: "error",
                          });
                        },
                      },
                    );
                  } else if (actionId === "tags") {
                    // Call rows don't carry tags — fetch current tags per
                    // trace so the popover can merge correctly. Guard
                    // against concurrent clicks triggering duplicate fetches.
                    if (tagsFetching) return;
                    const ids = (selectedCallIds || []).filter(Boolean);
                    if (ids.length === 0) return;
                    setTagsFetching(true);
                    Promise.all(
                      ids.map((id) =>
                        axios
                          .get(endpoints.project.getTrace(id))
                          .then((res) => ({
                            id,
                            type: "trace",
                            currentTags: res?.data?.result?.tags || [],
                          }))
                          .catch(() => ({
                            id,
                            type: "trace",
                            currentTags: [],
                          })),
                      ),
                    )
                      .then((items) => {
                        setTagsBulkItems(items);
                        setTagsAnchorEl(anchor);
                      })
                      .finally(() => setTagsFetching(false));
                  }
                  return;
                }
                const isSelectAll =
                  selectedTab === "trace"
                    ? allTracesSelected
                    : allSpansSelected;
                // Tags / annotate operate on enumerated IDs. ag-grid's
                // "Select all" inverts toggledNodes (it lists *deselected*
                // rows), so these actions can't target the full set without
                // a server-side filter-mode primitive. Once such a primitive
                // exists (Phase 2 for annotation-queue), the corresponding
                // action is removed from this guard.
                //
                // annotation-queue: permitted when the user has explicitly
                // opted into filter-mode selection via the banner
                // (`filterSelectionMode`). The popover's submit then
                // dispatches a filter-mode payload to the backend.
                if (isSelectAll && ["tags", "annotate"].includes(actionId)) {
                  enqueueSnackbar(
                    "Deselect 'all' and pick specific items for this action",
                    { variant: "info" },
                  );
                  return;
                }
                const activeFilterMode =
                  selectedTab === "trace"
                    ? filterSelectionMode
                    : spanFilterSelectionMode;
                if (
                  isSelectAll &&
                  !activeFilterMode &&
                  actionId === "annotation-queue"
                ) {
                  // Header select-all is on, but the user hasn't opted in
                  // via the banner yet. Defer the action until they do.
                  enqueueSnackbar(
                    "Use the 'Select all matching your filter' banner to add the full set, or deselect 'all' and pick specific rows.",
                    { variant: "info" },
                  );
                  return;
                }
                switch (actionId) {
                  case "dataset":
                    setOpenAddDataset(true);
                    break;
                  case "tags": {
                    // Snapshot the selection's current tags from the grid
                    // so the popover can merge against per-item state.
                    const grid = (
                      selectedTab === "trace"
                        ? primaryTraceGridRef
                        : primarySpanGridRef
                    ).current?.api;
                    const nodes = grid?.getSelectedNodes?.() || [];
                    setTagsBulkItems(
                      nodes
                        .map((n) => ({
                          id:
                            selectedTab === "trace"
                              ? n.data?.trace_id
                              : n.data?.span_id,
                          type: selectedTab === "trace" ? "trace" : "span",
                          currentTags: n.data?.tags || [],
                        }))
                        .filter((i) => i.id),
                    );
                    setTagsAnchorEl(anchor);
                    break;
                  }
                  case "annotation-queue":
                    setQueueAnchorEl(anchor);
                    break;
                  case "annotate":
                    setOpenAnnotateDrawer(true);
                    break;
                  default:
                    break;
                }
              }}
              isSimulator={projectSource === PROJECT_SOURCE.SIMULATOR}
              isSpansView={selectedTab === "spans"}
              excludeSimulationCalls={!!excludeSimulationCalls}
              onToggleSimulationCalls={() =>
                setExcludeSimulationCalls(excludeSimulationCalls ? null : true)
              }
              onAddEvals={handleAddEvals}
            />
            {/* FilterChips moved above graph section */}
            {/* Hidden: Old secondary Trace/Spans tabs — replaced by top-level tab bar */}
            <Box sx={{ display: "none" }}>
              <Box
                sx={{
                  borderBottom: 1,
                  borderColor: "divider",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  pr: theme.spacing(2),
                }}
              >
                <Tabs
                  value={selectedTab}
                  onChange={(e, value) => {
                    resetFilters();
                    resetColumns();
                    setSelectedTab(value);
                    resetSpanGridStore();
                    resetTraceGridStore();
                  }}
                  aria-label="change tabs"
                  sx={{
                    minHeight: 0,
                    "& .MuiTab-root": {
                      margin: "0 !important",
                      fontWeight: "600",
                      typography: "s1",
                      color: "primary.main",
                      "&:not(.Mui-selected)": {
                        color: "text.disabled",
                        fontWeight: "500",
                      },
                    },
                  }}
                  TabIndicatorProps={{
                    style: {
                      backgroundColor: theme.palette.primary.main,
                    },
                  }}
                >
                  <Tab
                    label="Trace"
                    value="trace"
                    sx={{
                      margin: theme.spacing(0),
                      px: theme.spacing(1.875),
                    }}
                  />
                  <Tab
                    label="Spans"
                    value="spans"
                    sx={{
                      margin: theme.spacing(0),
                      px: theme.spacing(1.875),
                    }}
                  />
                </Tabs>

                <Box
                  display={"flex"}
                  gap={theme.spacing(1)}
                  alignItems={"center"}
                >
                  <Box>
                    {currentGridRef.current?.api && (
                      <TotalRowsStatusBar api={currentGridRef.current?.api} />
                    )}
                  </Box>
                  <Divider
                    orientation="vertical"
                    flexItem
                    sx={{ my: theme.spacing(1) }}
                  />{" "}
                  <Button
                    sx={{
                      padding: 0,
                      width: 30,
                      height: 30,
                      margin: 0,
                      minWidth: 0,
                      "& .MuiButton-startIcon": {
                        margin: 0,
                      },
                    }}
                    ref={columnConfigureRef}
                    onClick={() => setOpenColumnConfigure(true)}
                  >
                    <SvgColor
                      src="/assets/icons/action_buttons/ic_column.svg"
                      sx={{ height: "16px", width: "16px" }}
                    />
                  </Button>
                  <Divider
                    orientation="vertical"
                    flexItem
                    sx={{ my: theme.spacing(1) }}
                  />{" "}
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "row",
                      alignItems: "center",
                      gap: 2,
                    }}
                  >
                    <ShowComponent
                      condition={
                        !!columnKey &&
                        columns[columnKey]?.some(
                          (col) => col?.groupBy === "Evaluation Metrics",
                        )
                      }
                    >
                      <Button
                        sx={{
                          padding: "12px",
                          borderColor: hasEvalFilter
                            ? "action.selected"
                            : undefined,
                        }}
                        variant="outlined"
                        size="small"
                        onClick={() => setHasEvalFilter(!hasEvalFilter)}
                        startIcon={
                          <Iconify
                            icon={
                              hasEvalFilter
                                ? "famicons:checkbox"
                                : "system-uicons:checkbox-empty"
                            }
                            color={
                              hasEvalFilter ? "primary.main" : "text.disabled"
                            }
                            width={22}
                          />
                        }
                      >
                        {`Show ${selectedTab === "trace" ? "Traces" : "Spans"} with Evals`}
                      </Button>
                    </ShowComponent>

                    <CustomTooltip
                      show={true}
                      title={
                        autoSizeAllCols
                          ? "Reset column size"
                          : "Automatically resizes all columns based on their content"
                      }
                      placement="bottom"
                      arrow
                      size="small"
                      type="black"
                      slotProps={{
                        tooltip: {
                          sx: {
                            maxWidth: "200px !important",
                          },
                        },
                        popper: {
                          modifiers: {
                            name: "preventOverflow",
                            options: {
                              boundary: "viewport",
                              padding: 12,
                            },
                          },
                        },
                      }}
                    >
                      <Button
                        sx={{ height: "30px" }}
                        variant="outlined"
                        size="medium"
                        onClick={handleAutoSize}
                        startIcon={
                          !autoSizeAllCols ? (
                            <SvgColor
                              src="/assets/icons/ic_autosize_columns.svg"
                              width={16}
                              height={16}
                            />
                          ) : (
                            <SvgColor
                              src="/assets/icons/ic_reload.svg "
                              width={16}
                              height={16}
                            />
                          )
                        }
                      >
                        {autoSizeAllCols ? "Reset Columns" : "Autosize Columns"}
                      </Button>
                    </CustomTooltip>
                    {/* <ShowComponent condition={selectedTab === "trace"}>
                    <ReplayTraces
                      gridApi={
                        selectedGraph === "primary"
                          ? primaryTraceGridRef.current?.api
                          : compareTraceGridRef.current?.api
                      }
                    />
                  </ShowComponent> */}
                    <CustomTooltip
                      show={true}
                      title="All the data points will get added as a dataset"
                      placement="bottom"
                      arrow
                      size="small"
                      type="black"
                      slotProps={{
                        tooltip: {
                          sx: {
                            maxWidth: "200px !important",
                          },
                        },
                        popper: {
                          modifiers: {
                            name: "preventOverflow",
                            options: {
                              boundary: "viewport",
                              padding: 12,
                            },
                          },
                        },
                      }}
                    >
                      <span>
                        <Button
                          variant="contained"
                          color="primary"
                          sx={{
                            borderRadius: theme.spacing(1),
                            height: "30px",
                            "&:disabled": {
                              color: "common.white",
                              backgroundColor: "action.hover",
                            },
                          }}
                          startIcon={
                            <Iconify
                              icon="line-md:plus"
                              color="background.paper"
                              width="16px"
                              height="16px"
                            />
                          }
                          onClick={() => setOpenAddDataset(true)}
                          disabled={
                            !RolePermission.DATASETS[PERMISSIONS.UPDATE][
                              role
                            ] ||
                            (selectedTab === "trace"
                              ? !(
                                  selectedTraces?.length > 0 ||
                                  allTracesSelected
                                )
                              : !(
                                  selectedSpans?.length > 0 || allSpansSelected
                                ))
                          }
                        >
                          <Typography
                            typography="s2"
                            fontWeight={"fontWeightMedium"}
                          >
                            Add to dataset
                          </Typography>
                        </Button>
                      </span>
                    </CustomTooltip>
                  </Box>
                </Box>
                <AddDataset
                  handleClose={() => {
                    setOpenAddDataset(false);
                  }}
                  actionToDataset={openAddDataset}
                  selectedTraces={(() => {
                    if (projectSource === PROJECT_SOURCE.SIMULATOR) {
                      // Simulator calls are traces under the hood
                      return (selectedCallIds || []).filter(Boolean);
                    }
                    return (
                      selectedTraces?.filter((id) => id != null && id !== "") ||
                      []
                    );
                  })()}
                  selectedSpans={
                    selectedSpans?.filter((id) => id != null && id !== "") || []
                  }
                  currentTab={
                    projectSource === PROJECT_SOURCE.SIMULATOR
                      ? "trace"
                      : selectedTab
                  }
                  selectAll={
                    projectSource === PROJECT_SOURCE.SIMULATOR
                      ? false
                      : selectedTab === "trace"
                        ? allTracesSelected
                        : allSpansSelected
                  }
                  onSuccess={() => {
                    if (projectSource === PROJECT_SOURCE.SIMULATOR) {
                      primaryCallLogsGridRef.current?.deselectAll?.();
                      compareCallLogsGridRef.current?.deselectAll?.();
                      setSelectedCallIds([]);
                      return;
                    }
                    if (selectedTab === "trace") {
                      primaryTraceGridRef.current?.api?.deselectAll();
                      if (selectedGraph === "compare") {
                        compareTraceGridRef.current?.api?.deselectAll();
                      }
                    } else {
                      primarySpanGridRef.current?.api?.deselectAll();
                      if (selectedGraph === "compare") {
                        compareSpanGridRef.current?.api?.deselectAll();
                      }
                    }
                  }}
                />
              </Box>
            </Box>
            {/* end hidden old toolbar */}

            {/* Bulk action dialogs (portal-mounted) */}
            <Suspense fallback={null}>
              <AddTagsPopover
                open={Boolean(tagsAnchorEl)}
                anchorEl={tagsAnchorEl}
                onClose={() => {
                  // onClose (not onSuccess) does the deselect/refresh —
                  // the popover stays open across multiple tag adds; we
                  // only want to flush selection state when the user is
                  // done and closes the popover.
                  if (projectSource === PROJECT_SOURCE.SIMULATOR) {
                    primaryCallLogsGridRef.current?.deselectAll?.();
                    compareCallLogsGridRef.current?.deselectAll?.();
                    setSelectedCallIds([]);
                  } else if (selectedTab === "trace") {
                    primaryTraceGridRef.current?.api?.refreshServerSide?.({
                      purge: true,
                    });
                    primaryTraceGridRef.current?.api?.deselectAll?.();
                  } else {
                    primarySpanGridRef.current?.api?.refreshServerSide?.({
                      purge: true,
                    });
                    primarySpanGridRef.current?.api?.deselectAll?.();
                  }
                  setTagsAnchorEl(null);
                  setTagsBulkItems([]);
                }}
                bulkItems={tagsBulkItems}
              />
            </Suspense>
            <Suspense fallback={null}>
              <AddToQueueDialog
                anchorEl={queueAnchorEl}
                onClose={() => setQueueAnchorEl(null)}
                sourceType={(() => {
                  // Simulator projects surface voice calls whose selected IDs
                  // come from CallLogsGrid as `row.trace_id` — send them as
                  // traces, not call_executions.
                  if (projectSource === PROJECT_SOURCE.SIMULATOR)
                    return "trace";
                  return selectedTab === "trace" ? "trace" : "observation_span";
                })()}
                sourceIds={(() => {
                  // In filter mode, `sourceIds` carries the *excluded*
                  // (deselected) IDs — the backend subtracts them from the
                  // full filter match set server-side. Simulator filter
                  // mode currently always posts an empty exclude_ids; per-row
                  // deselection after opt-in is a follow-up (client-side
                  // CallLogsGrid has no inverted-selection model).
                  if (filterSelectionMode && selectedTab === "trace") {
                    return selectedTraces || [];
                  }
                  if (spanFilterSelectionMode && selectedTab === "spans") {
                    return selectedSpans || [];
                  }
                  if (
                    simCallFilterSelectionMode &&
                    projectSource === PROJECT_SOURCE.SIMULATOR
                  ) {
                    return [];
                  }
                  if (projectSource === PROJECT_SOURCE.SIMULATOR)
                    return (selectedCallIds || []).filter(Boolean);
                  return selectedTab === "trace"
                    ? (selectedTraces || []).filter(Boolean)
                    : (selectedSpans || []).filter(Boolean);
                })()}
                itemName={(() => {
                  if (projectSource === PROJECT_SOURCE.SIMULATOR) return "Call";
                  return selectedTab === "trace" ? "Trace" : "Span";
                })()}
                selectionMode={(() => {
                  if (filterSelectionMode && selectedTab === "trace")
                    return "filter";
                  if (spanFilterSelectionMode && selectedTab === "spans")
                    return "filter";
                  if (
                    simCallFilterSelectionMode &&
                    projectSource === PROJECT_SOURCE.SIMULATOR
                  )
                    return "filter";
                  return "manual";
                })()}
                filter={(() => {
                  // Mirror exactly what each grid sends for its list fetch so
                  // the backend filter-mode resolver matches the same rows
                  // the user sees. Missing any of extraFilters / metricFilters
                  // / hasEvalFilter here causes the resolver to match a wider
                  // set than the grid, leading to the bug where "N selected"
                  // under a chip/metric filter adds MORE than N to the queue.
                  if (filterSelectionMode && selectedTab === "trace") {
                    return [
                      ...objectCamelToSnake([
                        ...primaryCombinedFilters,
                        ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                      ]),
                      ...(extraFilters || []),
                      ...(metricFilters || []),
                    ];
                  }
                  if (spanFilterSelectionMode && selectedTab === "spans") {
                    return [
                      ...objectCamelToSnake([
                        ...primarySpanValidatedFilters,
                        ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                      ]),
                      ...(extraFilters || []),
                      ...(metricFilters || []),
                    ];
                  }
                  if (
                    simCallFilterSelectionMode &&
                    projectSource === PROJECT_SOURCE.SIMULATOR
                  ) {
                    return [
                      ...objectCamelToSnake([
                        ...primaryCombinedFilters,
                        ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                      ]),
                      ...(extraFilters || []),
                      ...(metricFilters || []),
                    ];
                  }
                  return null;
                })()}
                projectId={
                  (filterSelectionMode && selectedTab === "trace") ||
                  (spanFilterSelectionMode && selectedTab === "spans") ||
                  (simCallFilterSelectionMode &&
                    projectSource === PROJECT_SOURCE.SIMULATOR)
                    ? observeId
                    : null
                }
                isVoiceCall={projectSource === PROJECT_SOURCE.SIMULATOR}
                removeSimulationCalls={
                  projectSource === PROJECT_SOURCE.SIMULATOR
                    ? !!excludeSimulationCalls
                    : false
                }
                onSuccess={() => {
                  if (filterSelectionMode && selectedTab === "trace") {
                    setFilterSelectionMode(false);
                    primaryTraceGridRef.current?.api?.deselectAll();
                    return;
                  }
                  if (spanFilterSelectionMode && selectedTab === "spans") {
                    setSpanFilterSelectionMode(false);
                    primarySpanGridRef.current?.api?.deselectAll();
                    return;
                  }
                  if (
                    simCallFilterSelectionMode &&
                    projectSource === PROJECT_SOURCE.SIMULATOR
                  ) {
                    setSimCallFilterSelectionMode(false);
                    primaryCallLogsGridRef.current?.deselectAll?.();
                    compareCallLogsGridRef.current?.deselectAll?.();
                    setSelectedCallIds([]);
                    return;
                  }
                  if (projectSource === PROJECT_SOURCE.SIMULATOR) {
                    primaryCallLogsGridRef.current?.deselectAll?.();
                    compareCallLogsGridRef.current?.deselectAll?.();
                    setSelectedCallIds([]);
                    return;
                  }
                  if (selectedTab === "trace") {
                    primaryTraceGridRef.current?.api?.deselectAll();
                  } else {
                    primarySpanGridRef.current?.api?.deselectAll();
                  }
                }}
              />
            </Suspense>
            <Suspense fallback={null}>
              <AnnotateDrawer
                open={openAnnotateDrawer}
                onClose={() => setOpenAnnotateDrawer(false)}
                projectId={observeId}
                listSpanId={
                  selectedTab === "spans" ? selectedSpans?.[0] || null : null
                }
                voiceObserveSpanId={null}
                runName=""
                observationType={selectedTab === "trace" ? "Trace" : "Span"}
                observationName=""
                onSubmit={(data) => {
                  const spanId =
                    selectedTab === "spans" ? selectedSpans?.[0] : null;
                  if (!spanId) {
                    enqueueSnackbar("Select a single span to annotate", {
                      variant: "warning",
                    });
                    return;
                  }
                  const { notes, ...rest } = data || {};
                  addAnnotationValues({
                    observation_span_id: spanId,
                    annotation_values: Object.fromEntries(
                      Object.entries(rest).filter(([, value]) => value !== ""),
                    ),
                    notes,
                  });
                }}
              />
            </Suspense>
            <Suspense>
              <ColumnConfigureDropDown
                open={openColumnConfigure}
                onClose={() => {
                  setOpenColumnConfigure(false);
                  setColumnConfigureAnchor(null);
                }}
                anchorEl={columnConfigureAnchor}
                columns={columns[columnKey]}
                onColumnVisibilityChange={onColumnVisibilityChange}
                useGrouping
                placement="right"
                setColumns={(updatedColumns) =>
                  setColumns((prev) => ({
                    ...prev,
                    [columnKey]: updatedColumns,
                  }))
                }
              />
            </Suspense>
            <CustomColumnDialog
              open={openCustomColumn}
              onClose={() => setOpenCustomColumn(false)}
              attributes={attributes}
              existingColumns={columns[columnKey]}
              onAddColumns={handleAddCustomColumns}
              onRemoveColumns={handleRemoveCustomColumns}
            />
            <Box sx={{ paddingTop: 2 }}>
              <SelectAllBanner
                visible={
                  selectedTab === "trace" &&
                  projectSource !== PROJECT_SOURCE.SIMULATOR &&
                  allTracesSelected &&
                  !filterSelectionMode
                }
                visibleCount={
                  primaryTraceGridRef.current?.api?.getDisplayedRowCount?.() ||
                  0
                }
                totalMatching={totalTraces || 0}
                noun="trace"
                onSelectAll={() => setFilterSelectionMode(true)}
              />
              <Box
                sx={{
                  display:
                    selectedTab === "trace" &&
                    selectedGraph === "primary" &&
                    projectSource !== PROJECT_SOURCE.SIMULATOR
                      ? "block"
                      : "none",
                }}
              >
                <Suspense fallback={<ComponentLoader />}>
                  <TraceGrid
                    columns={columns["primary-trace"]}
                    setColumns={(columns) =>
                      setSpecificColumns("primary-trace", columns)
                    }
                    filters={primaryTraceValidatedFilters}
                    extraFilters={extraFilters}
                    ref={primaryTraceGridRef}
                    setFilters={setPrimaryTraceFilters}
                    setExtraFilters={setExtraFilters}
                    setFilterOpen={setIsPrimaryFilterOpen}
                    setLoading={setLoadingEnhanced}
                    compareType="primary"
                    projectId={observeId}
                    hasEvalFilter={hasEvalFilter}
                    cellHeight={cellHeight}
                    metricFilters={metricFilters}
                    pendingCustomColumnsRef={primaryTracePendingRef}
                    showErrors={showErrors}
                    enabled={
                      [
                        PROJECT_SOURCE.PROTOTYPE,
                        PROJECT_SOURCE.OBSERVE,
                      ].includes(projectSource) && selectedTab === "trace"
                    }
                  />
                </Suspense>
              </Box>
              <Box
                sx={{
                  display:
                    selectedTab === "trace" &&
                    selectedGraph === "compare" &&
                    projectSource !== PROJECT_SOURCE.SIMULATOR
                      ? "block"
                      : "none",
                }}
              >
                <Suspense fallback={<ComponentLoader />}>
                  <TraceGrid
                    columns={columns["compare-trace"]}
                    setColumns={(columns) =>
                      setSpecificColumns("compare-trace", columns)
                    }
                    filters={compareTraceValidatedFilters}
                    extraFilters={extraFilters}
                    ref={compareTraceGridRef}
                    setFilters={setCompareTraceFilters}
                    setExtraFilters={setExtraFilters}
                    setFilterOpen={setIsPrimaryFilterOpen}
                    setLoading={setLoadingEnhanced}
                    compareType="compare"
                    hasEvalFilter={hasEvalFilter}
                    cellHeight={cellHeight}
                    metricFilters={metricFilters}
                    pendingCustomColumnsRef={compareTracePendingRef}
                    projectId={observeId}
                    showErrors={showErrors}
                    enabled={
                      [
                        PROJECT_SOURCE.PROTOTYPE,
                        PROJECT_SOURCE.OBSERVE,
                      ].includes(projectSource) && selectedTab === "trace"
                    }
                  />
                </Suspense>
              </Box>
              <Box
                sx={{
                  display:
                    selectedTab === "spans" &&
                    projectSource !== PROJECT_SOURCE.SIMULATOR
                      ? "block"
                      : "none",
                }}
              >
                <SelectAllBanner
                  visible={
                    selectedTab === "spans" &&
                    projectSource !== PROJECT_SOURCE.SIMULATOR &&
                    allSpansSelected &&
                    !spanFilterSelectionMode
                  }
                  visibleCount={
                    primarySpanGridRef.current?.api?.getDisplayedRowCount?.() ||
                    0
                  }
                  totalMatching={totalSpans || 0}
                  noun="span"
                  onSelectAll={() => setSpanFilterSelectionMode(true)}
                />
              </Box>
              <Box
                sx={{
                  display:
                    selectedTab === "spans" &&
                    selectedGraph === "primary" &&
                    projectSource !== PROJECT_SOURCE.SIMULATOR
                      ? "block"
                      : "none",
                }}
              >
                <Suspense fallback={<ComponentLoader />}>
                  <SpanGrid
                    columns={columns["primary-spans"]}
                    setColumns={(columns) =>
                      setSpecificColumns("primary-spans", columns)
                    }
                    filters={primarySpanValidatedFilters}
                    extraFilters={extraFilters}
                    ref={primarySpanGridRef}
                    hasEvalFilter={hasEvalFilter}
                    cellHeight={cellHeight}
                    metricFilters={metricFilters}
                    pendingCustomColumnsRef={primarySpansPendingRef}
                    setFilters={setPrimarySpanFilters}
                    setExtraFilters={setExtraFilters}
                    setFilterOpen={setIsPrimaryFilterOpen}
                    setLoading={setLoadingEnhanced}
                    compareType="primary"
                    enabled={
                      [
                        PROJECT_SOURCE.PROTOTYPE,
                        PROJECT_SOURCE.OBSERVE,
                      ].includes(projectSource) && selectedTab === "spans"
                    }
                  />
                </Suspense>
              </Box>
              <Box
                sx={{
                  display:
                    selectedTab === "spans" &&
                    selectedGraph === "compare" &&
                    projectSource !== PROJECT_SOURCE.SIMULATOR
                      ? "block"
                      : "none",
                }}
              >
                <Suspense fallback={<ComponentLoader />}>
                  <SpanGrid
                    columns={columns["compare-spans"]}
                    setColumns={(columns) =>
                      setSpecificColumns("compare-spans", columns)
                    }
                    hasEvalFilter={hasEvalFilter}
                    cellHeight={cellHeight}
                    metricFilters={metricFilters}
                    pendingCustomColumnsRef={compareSpansPendingRef}
                    filters={compareSpansValidatedFilters}
                    extraFilters={extraFilters}
                    ref={compareSpanGridRef}
                    setFilters={setCompareSpansFilters}
                    setExtraFilters={setExtraFilters}
                    setFilterOpen={setIsPrimaryFilterOpen}
                    setLoading={setLoadingEnhanced}
                    compareType="compare"
                    enabled={
                      [
                        PROJECT_SOURCE.PROTOTYPE,
                        PROJECT_SOURCE.OBSERVE,
                      ].includes(projectSource) && selectedTab === "spans"
                    }
                  />
                </Suspense>
              </Box>
            </Box>
          </>
          <Box
            sx={{
              display:
                projectSource === PROJECT_SOURCE.SIMULATOR &&
                selectedGraph === "primary"
                  ? "block"
                  : "none",
            }}
          >
            <SelectAllBanner
              visible={
                projectSource === PROJECT_SOURCE.SIMULATOR &&
                simCallMeta.isAllOnPageSelected &&
                simCallMeta.totalPages > 1 &&
                !simCallFilterSelectionMode
              }
              visibleCount={simCallMeta.currentPageSize}
              totalMatching={
                // Prefer the backend's exact count; fall back to the
                // paged upper bound only if the API hasn't reported it
                // yet (avoids "Select all 50" when 29 match).
                simCallMeta.totalMatching ??
                simCallMeta.totalPages * simCallMeta.pageLimit
              }
              noun="call"
              onSelectAll={() => setSimCallFilterSelectionMode(true)}
            />
            <Suspense fallback={<ComponentLoader />}>
              <CallLogsGrid
                ref={primaryCallLogsGridRef}
                module="project"
                id={observeId}
                enabled={projectSource === PROJECT_SOURCE.SIMULATOR}
                cellHeight={cellHeight}
                columnVisibility={columns["primary-trace"]}
                showErrors={showErrors}
                params={{
                  project_id: observeId,
                  remove_simulation_calls: excludeSimulationCalls,
                  filters: JSON.stringify([
                    ...objectCamelToSnake([
                      ...primaryCombinedFilters,
                      ...(extraFilters || []),
                      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                    ]),
                    ...(metricFilters || []),
                  ]),
                }}
                onRowClicked={handleRowClicked}
                onConfigLoaded={handleSimulatorConfigLoaded}
                onSelectionChanged={(ids) => setSelectedCallIds(ids)}
                onSelectionMeta={setSimCallMeta}
              />
            </Suspense>
          </Box>
          <Box
            sx={{
              display:
                projectSource === PROJECT_SOURCE.SIMULATOR &&
                selectedGraph === "compare"
                  ? "block"
                  : "none",
            }}
          >
            <Suspense fallback={<ComponentLoader />}>
              <CallLogsGrid
                ref={compareCallLogsGridRef}
                module="project"
                id={observeId}
                enabled={projectSource === PROJECT_SOURCE.SIMULATOR}
                cellHeight={cellHeight}
                columnVisibility={columns["compare-trace"]}
                showErrors={showErrors}
                hideDrawer
                params={{
                  project_id: observeId,
                  remove_simulation_calls: excludeSimulationCalls,
                  filters: JSON.stringify([
                    ...objectCamelToSnake([
                      ...compareCombinedFilters,
                      ...(compareExtraFilters || []),
                      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                    ]),
                    ...(metricFilters || []),
                  ]),
                }}
                onRowClicked={handleRowClicked}
                onConfigLoaded={handleSimulatorConfigLoaded}
                renderDrawer={false}
              />
            </Suspense>
          </Box>
        </Box>
      </Box>
      {/* Old LLMFiltersDrawer removed — replaced by FilterPanel in ObserveToolbar */}
    </Box>
  );
};

LLMTracingView.propTypes = {
  mode: PropTypes.oneOf(["project", "user"]),
  userIdForUserMode: PropTypes.string,
};

export default LLMTracingView;
