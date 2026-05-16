import {
  Badge,
  Box,
  Collapse,
  IconButton,
  useTheme,
  Divider,
} from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import UserTraceTabV2 from "./UserTraceTabV2";
import UserSessionsTab from "./UserSessionsTab";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
import {
  DEFAULT_DATE_FILTER,
  initialSessionVisibility,
  SessionFilterDefinition,
  tabsData,
  transformDateFilterToBackendFilters,
  userDefaultFilter,
  userTraceRowHeightMapping,
} from "../common";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { useUrlState } from "src/routes/hooks/use-url-state";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import { getComplexFilterValidation } from "src/components/ComplexFilter/common";
import { useDebounce } from "src/hooks/use-debounce";
import { getRandomId } from "src/utils/utils";
import useTraceSessionStore from "../Store/useTraceSessionStore";
import ColumnResizer from "src/components/ColumnResizer/ColumnResizer";
import CustomTabs from "../CustomMemoizedTabs";
import TotalRowsStatusBar from "src/sections/develop-detail/Common/TotalRowsStatusBar";

// Updated component using both stores
const UserTraceSessionSection = () => {
  const theme = useTheme();
  const [selectedProjectId] = useUrlState("projectId", null);
  const [dateFilter] = useUrlState("dateFilter", DEFAULT_DATE_FILTER);

  const {
    selectedTab,
    openSessionColumnConfigure,
    setSelectedTab,
    setCellHeight,
    setOpenSessionColumnConfigure,
    resetColumnsOnTabChange,
    openUserDetailFilter,
    toggleOpenUserDetailFilter,
  } = useTraceSessionStore();

  const [traceFilter, setTraceFilter] = useUrlState("traceFilter", []);
  const [sessionFilter, setSessionFilter] = useUrlState("sessionFilter", []);

  // Keep only filters in component state as requested
  const [filters, setFilters] = useState(() => {
    if (selectedTab === "traces" && traceFilter.length > 0) {
      return traceFilter;
    }
    if (selectedTab === "sessions" && sessionFilter.length > 0) {
      return sessionFilter;
    }
    return [{ ...userDefaultFilter, id: getRandomId() }];
  });

  // Single ref object
  const refs = useRef({
    columnConfigure: null,
    sessionColumnConfigure: null,
    resizer: null,
    previousValidatedFilters: [],
  });

  const traceGridRef = useRef(null);
  const sessionGridRef = useRef(null);

  const [openResizer, setOpenResizer] = useState(false);
  const [sessionColumns, setSessionColumns] = useState([]);
  // const [sessionUpdateObj, setSessionUpdateObj] = useState(
  //   initialSessionVisibility,
  // );
  const sessionUpdateObj = useMemo(() => {
    if (sessionColumns.length === 0) {
      return initialSessionVisibility;
    } else {
      const visibilityMap = sessionColumns.reduce((acc, col) => {
        acc[col.id] = col.isVisible;
        return acc;
      }, {});
      return visibilityMap;
    }
  }, [sessionColumns]);

  useEffect(() => {
    if (filters.length > 0) {
      if (selectedTab === "traces") {
        setTraceFilter(filters);
      } else if (selectedTab === "sessions") {
        setSessionFilter(filters);
      }
    }
  }, [filters, selectedTab, setTraceFilter, setSessionFilter]);

  // useEffect(() => {
  //   if (!sessionColumns) return;

  //   const visibilityMap = sessionColumns.reduce((acc, col) => {
  //     acc[col.id] = col.isVisible;
  //     return acc;
  //   }, {});
  //   setSessionUpdateObj(visibilityMap);
  // }, [sessionColumns]);

  const validatedFilters = useMemo(() => {
    if (!Array.isArray(filters)) return [];

    const flatFilters = filters.flat();
    const newValidatedFilters = flatFilters
      .map((filter) => {
        const result = getComplexFilterValidation(true).safeParse(filter);
        return result.success ? result.data : false;
      })
      .filter(Boolean);

    if (
      JSON.stringify(refs.current.previousValidatedFilters) ===
      JSON.stringify(newValidatedFilters)
    ) {
      return refs.current.previousValidatedFilters;
    }

    refs.current.previousValidatedFilters = newValidatedFilters;
    return newValidatedFilters;
  }, [JSON.stringify(filters)]);

  // Event handlers
  const resetFilters = useCallback(() => {
    const defaultFilters = [{ ...userDefaultFilter, id: getRandomId() }];
    setFilters(defaultFilters);

    // Clear URL state for current tab
    if (selectedTab === "traces") {
      setTraceFilter([]);
    } else if (selectedTab === "sessions") {
      setSessionFilter([]);
    }
  }, [selectedTab, setTraceFilter, setSessionFilter]);

  const handleTabChange = useCallback(
    (e, value) => {
      resetFilters();
      setSelectedTab(value);
      resetColumnsOnTabChange();
    },
    [resetFilters, setSelectedTab, resetColumnsOnTabChange],
  );

  const handleColumnConfigureClick = useCallback(() => {
    setOpenSessionColumnConfigure(true);
  }, [setOpenSessionColumnConfigure]);

  // Session column mutation
  const { mutate: updateSessionListColumnVisibility } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.updateSessionListColumnVisibility(), {
        project_id: selectedProjectId,
        visibility: data,
      }),
  });

  // Column visibility handlers
  const handleSessionVisibilityColumnChange = useCallback(
    (newUpdateObj) => {
      // Update columns first in the store
      setSessionColumns((cols) =>
        cols.map((col) => ({ ...col, isVisible: newUpdateObj[col.id] })),
      );
      // Then update the visibility object
      // setSessionUpdateObj(newUpdateObj); as column are been set the setsessionUpdateObj will also update
      // Finally call the API
      updateSessionListColumnVisibility(newUpdateObj);
    },
    [setSessionColumns, updateSessionListColumnVisibility],
  );

  // Debounce the validated filters at component level
  const debouncedValidatedFilters = useDebounce(validatedFilters, 500);

  // Calculate if filters are applied without useEffect
  const isFilterApplied = useMemo(() => {
    const hasValidFilters =
      debouncedValidatedFilters && debouncedValidatedFilters.length > 0;
    const hasNonDefaultFilters = filters.some((filter) => {
      const filterConfig = filter.filter_config || {};
      const defaultConfig = userDefaultFilter.filter_config || {};

      return (
        filter.column_id !== userDefaultFilter.column_id ||
        filterConfig.filter_op !== defaultConfig.filter_op ||
        filterConfig.filter_value !== defaultConfig.filter_value ||
        filterConfig.filter_type !== defaultConfig.filter_type ||
        (filterConfig.filter_value && filterConfig.filter_value.length > 0)
      );
    });

    return hasValidFilters || hasNonDefaultFilters;
  }, [debouncedValidatedFilters, filters]);

  // Processed filters
  const processedFilters = useMemo(() => {
    const finalDateFilters = transformDateFilterToBackendFilters(dateFilter);

    const combinedFilters = [
      ...(finalDateFilters || []),
      ...(debouncedValidatedFilters || []),
    ];

    return combinedFilters.length === 1
      ? [combinedFilters[0]]
      : combinedFilters;
  }, [dateFilter, debouncedValidatedFilters]);

  // Action buttons config
  const actionButtons = useMemo(
    () => [
      {
        icon: "ic_height",
        action: () => setOpenResizer(true),
        ref: "resizer",
        tooltip: "Column Size",
      },
      {
        icon: "ic_filter",
        action: toggleOpenUserDetailFilter,
        tooltip: "Filter",
        showBadge: isFilterApplied,
      },
      {
        icon: "ic_column",
        action: handleColumnConfigureClick,
        ref:
          selectedTab === "traces"
            ? "columnConfigure"
            : "sessionColumnConfigure",
        tooltip: "View Column",
      },
    ],
    [
      toggleOpenUserDetailFilter,
      handleColumnConfigureClick,
      selectedTab,
      isFilterApplied,
    ],
  );

  // Render helpers
  const renderActionButton = useCallback(
    (button, index) => (
      <CustomTooltip
        key={index}
        show
        title={button.tooltip}
        placement="bottom"
        arrow
        size="small"
      >
        <IconButton
          size="small"
          sx={{ color: "text.disabled" }}
          onClick={button.action}
          ref={button.ref ? (el) => (refs.current[button.ref] = el) : null}
        >
          {button.showBadge ? (
            <Badge
              variant="dot"
              color="error"
              overlap="circular"
              anchorOrigin={{ vertical: "top", horizontal: "right" }}
              sx={{ "& .MuiBadge-badge": { top: 1, right: 1 } }}
            >
              <SvgColor
                src={`/assets/icons/action_buttons/${button.icon}.svg`}
                sx={{ width: 16, height: 16, color: "text.primary" }}
              />
            </Badge>
          ) : (
            <SvgColor
              src={`/assets/icons/action_buttons/${button.icon}.svg`}
              sx={{ width: 16, height: 16, color: "text.primary" }}
            />
          )}
        </IconButton>
      </CustomTooltip>
    ),
    [],
  );

  const tabsComponent = useMemo(
    () => (
      <CustomTabs
        tabs={tabsData}
        selectedTab={selectedTab}
        onChange={handleTabChange}
      />
    ),
    [selectedTab, handleTabChange],
  );

  const currentGridRef = useMemo(() => {
    if (selectedTab === "traces") {
      return traceGridRef;
    }
    if (selectedTab === "sessions") {
      return sessionGridRef;
    }
    return null;
  }, [selectedTab]);

  return (
    <>
      <Box
        sx={{
          borderBottom: 1,
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        {tabsComponent}
        <Box display="flex" gap={theme.spacing(1)} alignItems="center">
          {selectedTab === "sessions" && (
            <TotalRowsStatusBar api={currentGridRef?.current?.api} />
          )}
          {selectedTab === "sessions" && (
            <>
              <Divider
                orientation="vertical"
                flexItem
                sx={{ my: theme.spacing(1) }}
              />{" "}
              {actionButtons.map(renderActionButton)}
            </>
          )}
        </Box>
      </Box>

      {selectedTab === "sessions" && (
        <Box>
          <Collapse in={openUserDetailFilter} sx={{ mb: 1 }} unmountOnExit>
            <Box sx={{ paddingY: "13px", paddingX: 1 }}>
              <ComplexFilter
                filters={filters}
                defaultFilter={userDefaultFilter}
                setFilters={setFilters}
                filterDefinition={SessionFilterDefinition}
                onClose={toggleOpenUserDetailFilter}
              />
            </Box>
          </Collapse>
        </Box>
      )}

      <Box sx={{ mt: 2 }}>
        <ShowComponent condition={selectedTab === "traces"}>
          <UserTraceTabV2 dateFilter={dateFilter} />
        </ShowComponent>

        <ShowComponent condition={selectedTab === "sessions"}>
          <UserSessionsTab
            sessionColumns={sessionColumns}
            setSessionColumns={setSessionColumns}
            sessionUpdateObj={sessionUpdateObj}
            filters={processedFilters}
            ref={sessionGridRef}
          />
        </ShowComponent>
      </Box>

      <ColumnConfigureDropDown
        open={openSessionColumnConfigure}
        onClose={() => setOpenSessionColumnConfigure(false)}
        anchorEl={refs.current.sessionColumnConfigure}
        columns={sessionColumns}
        onColumnVisibilityChange={handleSessionVisibilityColumnChange}
        setColumns={setSessionColumns}
        defaultGrouping="Session Columns"
      />
      <ColumnResizer
        open={openResizer}
        anchorEl={refs.current.resizer}
        sizeMapping={userTraceRowHeightMapping}
        onClose={() => setOpenResizer(false)}
        setCellHeight={setCellHeight}
      />
    </>
  );
};

export default UserTraceSessionSection;
