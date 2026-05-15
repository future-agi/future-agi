import {
  Badge,
  Box,
  Button,
  Collapse,
  Divider,
  IconButton,
  InputAdornment,
  styled,
  Typography,
  useTheme,
} from "@mui/material";
import React, {
  useRef,
  useEffect,
  useState,
  useMemo,
  useCallback,
} from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useTestDetailStore, useTestDetailStoreShallow } from "./states";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import logger from "../../utils/logger";
import { useTestDetail } from "./context/TestDetailContext";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
import {
  getSelectedCallExecutionIds,
  getTestRunDetailColumnQuery,
  getTestRunDetailFilterDefinition,
} from "./common";
import { useParams } from "react-router";
import { useGetTestRunDetailGridColumns } from "./common";
import { isValidFiltersChanged } from "src/hooks/use-get-validated-filters";
import TestDetailSelection from "./TestDetailSelection";
import ColumnConfigureDropDown from "../project-detail/ColumnDropdown/ColumnConfigureDropDown";
import { getRandomId } from "../../utils/utils";
import { ShowComponent } from "src/components/show";
import { useQuery } from "@tanstack/react-query";
import { AGENT_TYPES } from "../agents/constants";

const defaultFilter = {
  columnId: "",
  filterConfig: {
    filterType: "",
    filterOp: "",
    filterValue: "",
  },
};

const StyledSmallButton = styled(IconButton)(({ theme }) => ({
  borderRadius: theme.spacing(0.5),
  border: `1px solid ${theme.palette.divider}`,
  padding: theme.spacing(0.625),
  color: "text.primary",
}));

const transformColDefToColumnStructure = (colDefs) => {
  const columnStructure = colDefs?.reduce((acc, col) => {
    if (col?.children) {
      const innerCols = col?.children?.map((childCol) => ({
        ...childCol,
        name: childCol?.headerName,
        groupBy: col?.headerName,
      }));
      acc.push(...innerCols);
    } else {
      acc.push({ ...col, name: col?.headerName });
    }

    return acc;
  }, []);

  return columnStructure;
};

const transformColumnStructureToColDef = (columnStructure) => {
  const DEFAULT_GROUP_NAME = getRandomId();
  const colDefs = [];
  const groupOrder = [];
  const groupMap = {};

  columnStructure.forEach((col) => {
    if (col?.groupBy && !groupOrder.includes(col?.groupBy)) {
      groupOrder.push(col?.groupBy);
    } else if (!col?.groupBy && !groupOrder.includes(DEFAULT_GROUP_NAME)) {
      groupOrder.push(DEFAULT_GROUP_NAME);
    }
    if (col?.groupBy) {
      groupMap[col?.groupBy] = [...(groupMap[col?.groupBy] || []), col];
    } else {
      groupMap[DEFAULT_GROUP_NAME] = [
        ...(groupMap[DEFAULT_GROUP_NAME] || []),
        col,
      ];
    }
  });

  groupOrder.forEach((groupName) => {
    if (groupName === DEFAULT_GROUP_NAME) {
      colDefs.push(...groupMap[DEFAULT_GROUP_NAME]);
    } else if (groupName === "Annotation Metrics") {
      colDefs.push(...(groupMap[groupName] || []));
    } else {
      colDefs.push({
        headerName: groupName,
        children: groupMap[groupName] || [],
      });
    }
  });

  return colDefs;
};

const TestRunDetailBar = () => {
  const { search, setSearch } = useTestDetailStoreShallow((state) => ({
    search: state.search,
    setSearch: state.setSearch,
  }));
  const { executionId } = useParams();
  const theme = useTheme();
  const {
    setOpenFilter,
    setOpenColumnConfigure,
    openColumnConfigure,
    openFilter,
    setFilters,
    filters,
    selectedFixableRecommendations,
    selectedNonFixableRecommendations,
    removeAllFilters,
  } = useTestDetailStoreShallow((state) => ({
    setOpenFilter: state.setOpenFilter,
    setOpenColumnConfigure: state.setOpenColumnConfigure,
    openColumnConfigure: state.openColumnConfigure,
    openFilter: state.openFilter,
    setFilters: state.setFilters,
    filters: state.filters,
    selectedFixableRecommendations: state.selectedFixableRecommendations,
    selectedNonFixableRecommendations: state.selectedNonFixableRecommendations,
    removeAllFilters: state.removeAllFilters,
  }));

  const { data: testExecutions } = useQuery({
    ...getTestRunDetailColumnQuery(executionId, 0, "", []),
    enabled: false,
    select: (data) => data?.data,
  });

  const callSelectedCount = useMemo(() => {
    return getSelectedCallExecutionIds().length;
  }, [selectedFixableRecommendations, selectedNonFixableRecommendations]);

  const isData = true;
  const isFilterApplied = useMemo(() => {
    const isFilters = filters?.some((f) =>
      f?.filterConfig?.filterValue &&
      Array.isArray(f?.filterConfig?.filterValue)
        ? f?.filterConfig?.filterValue?.length > 0
        : f?.filterConfig?.filterValue !== "",
    );

    return (
      isFilters ||
      selectedFixableRecommendations?.length > 0 ||
      selectedNonFixableRecommendations?.length > 0
    );
  }, [
    filters,
    selectedFixableRecommendations,
    selectedNonFixableRecommendations,
  ]);
  const columnConfigureRef = useRef(null);
  const columns = useGetTestRunDetailGridColumns(executionId);
  // const columnDefs = useMemo(() => {
  //   return getTestRunDetailGridColumnDefs(columns);
  // }, [columns]);

  const filterDefinition = useMemo(
    () => getTestRunDetailFilterDefinition(columns),
    [columns],
  );

  const { getGridApi } = useTestDetail();
  const gridApi = getGridApi();

  // State to store the latest column definitions from gridApi
  const [gridColumnDefs, setGridColumnDefs] = useState([]);

  // Watch for column changes using gridApi
  useEffect(() => {
    if (!gridApi) return;

    // Get initial column definitions
    const initialColumnDefs = gridApi.getColumnDefs();
    setGridColumnDefs(transformColDefToColumnStructure(initialColumnDefs));

    // Listen for column-related events to update column definitions
    const handleColumnChange = () => {
      const latestColumnDefs = gridApi.getColumnDefs();

      setGridColumnDefs(transformColDefToColumnStructure(latestColumnDefs));
    };

    // Add event listeners for various column events
    const columnEvents = [
      "columnVisible",
      "columnPinned",
      "columnResized",
      "columnMoved",
      "columnValueChanged",
      "newColumnsLoaded",
      "gridColumnsChanged",
    ];

    columnEvents.forEach((eventType) => {
      gridApi.addEventListener(eventType, handleColumnChange);
    });

    // Cleanup event listeners
    return () => {
      columnEvents.forEach((eventType) => {
        gridApi.removeEventListener(eventType, handleColumnChange);
      });
    };
  }, [gridApi]);

  const iconActionButtons = [
    {
      icon: "ic_column",
      action: () => setOpenColumnConfigure(true),
      tooltip: "View Column",
      ref: columnConfigureRef,
    },
    {
      icon: "ic_filter",
      action: () => setOpenFilter((b) => !b),
      tooltip: "Filter",
    },
  ];

  // Handle column visibility change in AG Grid
  const handleColumnVisibilityChange = (newVisibilityMap) => {
    if (!gridApi) return;

    const latestColumnDefs = gridApi.getColumnDefs();

    const newColumnDefs = latestColumnDefs?.map((col) => {
      if (col?.children) {
        const newChildMap = col?.children?.map((child) => {
          return {
            ...child,
            hide: !newVisibilityMap[child.colId],
            isVisible: newVisibilityMap[child.colId],
          };
        });
        return {
          ...col,
          children: newChildMap,
        };
      } else {
        return {
          ...col,
          hide: !newVisibilityMap[col.colId],
          isVisible: newVisibilityMap[col.colId],
        };
      }
    });

    try {
      gridApi.setGridOption("columnDefs", newColumnDefs);
    } catch (error) {
      logger.error("Error setting column visibility:", error);
    }
  };

  // Handle column reordering in AG Grid
  const handleSetColumns = (newColumns) => {
    if (!gridApi || !Array.isArray(newColumns)) return;

    logger.debug("handleSetColumns | newColumns", { newColumns });

    try {
      gridApi.setGridOption(
        "columnDefs",
        transformColumnStructureToColDef(newColumns),
      );
    } catch (error) {
      logger.error("Error setting column visibility:", error);
    }
  };

  const handleSetFilters = useCallback(
    (filterFunction) => {
      const filters = useTestDetailStore.getState().filters;

      const newFilters = filterFunction(filters);
      setFilters(filterFunction);
      if (isValidFiltersChanged(filters, newFilters)) {
        gridApi?.onFilterChanged?.();
      }
    },
    [setFilters, gridApi],
  );

  const iconStyles = {
    width: 16,
    height: 16,
    color: isData ? "text.primary" : "divider",
  };

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "center",
          justifyContent: "space-between",
          minHeight: "40px",
        }}
      >
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <FormSearchField
            size="small"
            placeholder="Search"
            searchQuery={search}
            onChange={(e) => {
              setSearch(e.target.value);
            }}
            sx={{
              width: "279px",
              "& .MuiInputBase-input": {
                paddingY: `${theme.spacing(0.5)}`,
                paddingRight: `${theme.spacing(0.5)}`,
              },
            }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SvgColor
                    src={`/assets/icons/custom/search.svg`}
                    sx={{
                      width: "20px",
                      height: "20px",
                      color: "text.disabled",
                    }}
                  />
                </InputAdornment>
              ),
              endAdornment: search && (
                <InputAdornment position="end">
                  <Iconify
                    icon="mingcute:close-line"
                    onClick={() => {
                      setSearch("");
                    }}
                    sx={{ color: "text.disabled", cursor: "pointer" }}
                  />
                </InputAdornment>
              ),
            }}
            inputProps={{
              sx: {
                padding: 0,
              },
            }}
          />
          {iconActionButtons.map((button, index) => (
            <CustomTooltip
              key={index}
              show={true}
              title={button.tooltip || ""}
              placement="bottom"
              arrow
              size="small"
            >
              <Badge
                variant="dot"
                color="error"
                overlap="circular"
                anchorOrigin={{ vertical: "top", horizontal: "right" }}
                sx={{
                  "& .MuiBadge-badge": {
                    top: 1,
                    right: 1,
                  },
                }}
                invisible={!(isFilterApplied && button?.icon === "ic_filter")}
              >
                <StyledSmallButton
                  size="small"
                  sx={{ color: "text.disabled" }}
                  ref={button.ref}
                  onClick={button.action}
                >
                  <SvgColor
                    src={`/assets/icons/action_buttons/${button.icon}.svg`}
                    sx={iconStyles}
                  />
                </StyledSmallButton>
              </Badge>
            </CustomTooltip>
          ))}
          <ShowComponent condition={isFilterApplied}>
            <Divider orientation="vertical" flexItem />
            <CustomTooltip
              show={true}
              title="Remove all filters"
              placement="bottom"
              arrow
              size="small"
            >
              <Button
                size="small"
                variant="outlined"
                sx={{ ...theme.typography.s2_1, fontWeight: 500, paddingX: 2 }}
                startIcon={
                  <Iconify icon="akar-icons:cross" width={8} height={8} />
                }
                onClick={() => {
                  removeAllFilters();
                  if (gridApi) {
                    gridApi?.onFilterChanged?.();
                  }
                }}
              >
                Remove Filters
              </Button>
            </CustomTooltip>
          </ShowComponent>
        </Box>
        <TestDetailSelection agentType={testExecutions?.agent_type} />
      </Box>
      <Collapse in={openFilter}>
        <Box sx={{ paddingTop: 2 }}>
          <ComplexFilter
            filters={filters}
            defaultFilter={defaultFilter}
            setFilters={handleSetFilters}
            filterDefinition={filterDefinition}
            onClose={() => setOpenFilter(false)}
          />
        </Box>
      </Collapse>
      <Box>
        <ShowComponent condition={isFilterApplied}>
          <Typography variant="s2" fontWeight="fontWeightMedium">
            {` (${callSelectedCount}) ${testExecutions?.agent_type === AGENT_TYPES.CHAT ? "Chats" : "Calls"} selected`}
          </Typography>
        </ShowComponent>
        <ShowComponent
          condition={!isFilterApplied && testExecutions?.count !== undefined}
        >
          <Typography variant="s2" fontWeight="fontWeightMedium">
            {` All  ${testExecutions?.agent_type === AGENT_TYPES.CHAT ? "Chats" : "Calls"} (${testExecutions?.count})`}
          </Typography>
        </ShowComponent>
      </Box>
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={gridColumnDefs}
        onColumnVisibilityChange={handleColumnVisibilityChange}
        setColumns={handleSetColumns}
        defaultGrouping="Call Details"
        useGrouping
      />
    </Box>
  );
};

export default TestRunDetailBar;
