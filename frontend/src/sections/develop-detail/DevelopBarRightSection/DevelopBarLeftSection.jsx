import {
  Badge,
  Box,
  Divider,
  IconButton,
  InputAdornment,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState, useCallback, useRef } from "react";
import ColumnResizer from "src/components/ColumnResizer/ColumnResizer";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify/iconify";
import SvgColor from "src/components/svg-color/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { defaultRowHeightMapping } from "src/utils/constants";
import { Events } from "src/utils/Mixpanel/EventNames";
import { trackEvent } from "src/utils/Mixpanel/mixpanel";
import {
  useDatasetOriginStore,
  useDevelopCellHeight,
  useDevelopFilterStoreShallow,
  useDevelopSearchStore,
} from "../states";
import { useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getDatasetQueryKey,
  getDatasetQueryOptions,
  useDevelopDatasetList,
  useGetColumns,
} from "src/api/develop/develop-detail";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import _ from "lodash";
import ColumnDropdown from "src/components/ColumnDropdown/ColumnDropdown";
import axios, { endpoints } from "src/utils/axios";

const DevelopBarLeftSection = ({
  resizerRef = {},
  hideFilter = false,
  hideColumnView = false,
  hideSearch = false,
  hideRowHeight = false,
}) => {
  const { dataset } = useParams();
  const { setDevelopFilterOpen, filters } = useDevelopFilterStoreShallow(
    (s) => ({
      setDevelopFilterOpen: s.setDevelopFilterOpen,
      filters: s.filters,
    }),
  );
  const columnConfigureRef = useRef(null);
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const columns = useGetColumns(dataset);
  const queryClient = useQueryClient();

  const isFilterApplied = useMemo(() => {
    const hasActiveFilter = filters?.some((f) =>
      f.filterConfig?.filterValue && Array.isArray(f.filterConfig.filterValue)
        ? f.filterConfig.filterValue.length > 0
        : f.filterConfig.filterValue !== "",
    );
    console.log("[TH-6624] BarLeftSection recompute isFilterApplied", {
      hasActiveFilter,
      filters,
    });
    return hasActiveFilter;
  }, [filters]);

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );

  const { gridApi } = useDevelopDetailContext();

  const { processingComplete } = useDatasetOriginStore();

  const isData = Boolean(tableData?.data?.result?.table?.length);
  const isSyntheticDataset = Boolean(tableData?.data?.result?.syntheticDataset);
  const theme = useTheme();
  const { search, setSearch } = useDevelopSearchStore();
  const setCellHeight = useDevelopCellHeight((s) => s.setCellHeight);

  //eslint-disable-next-line react-hooks/exhaustive-deps
  const debouncedSearch = useCallback(
    _.debounce(() => {
      gridApi?.current?.onFilterChanged();
    }, 300),
    [gridApi],
  );

  const setSearchExtended = (value) => {
    setSearch(value);
    debouncedSearch();
  };

  const [openResizer, setOpenResizer] = useState(false);
  const iconActionButtons = [
    {
      icon: "ic_height",
      action: () => setOpenResizer(true),
      event: null,
      ref: resizerRef,
      tooltip: "Column Size",
    },
    {
      icon: "ic_column",
      action: () => setOpenColumnConfigure(true),
      ref: columnConfigureRef,
      tooltip: "View Column",
    },
    {
      icon: "ic_filter",
      action: () => setDevelopFilterOpen((b) => !b),
      event: Events.datasetFilterSelected,
      tooltip: "Filter",
    },
  ];
  const iconStyles = {
    width: 16,
    height: 16,
    color: isData ? "text.primary" : "text.disabled",
  };

  const handleHeightSelect = (mappingObject) => {
    const height = mappingObject.height;
    if (gridApi && gridApi.current) {
      gridApi?.current?.api.forEachNode((node) => {
        node.setRowHeight(height);
      });
    }

    gridApi?.current?.api.onRowHeightChanged();
  };

  const { data: datasetList } = useDevelopDatasetList();

  const currentDataset = datasetList?.find((v) => v.datasetId === dataset);

  const { mutate: updateDataset } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
  });

  const onColumnVisibilityChange = (columnId) => {
    const columnOrder = [];
    const columnConfig = {};
    queryClient.setQueryData(
      getDatasetQueryKey(dataset, 0, [], [], ""),
      (oldData) => {
        const existingColumnOrder = oldData?.data?.result?.columnConfig;

        const newColumnOrder = existingColumnOrder.map((col) => {
          columnOrder.push(col.id);
          const currentVisible = col.is_visible;
          const nextVisible =
            col.id === columnId ? !currentVisible : currentVisible;
          columnConfig[col.id] = {
            is_visible: nextVisible,
            is_frozen: col.pinned,
          };
          // Update BOTH snake_case (canonical) and camelCase (alias) keys
          // so downstream reads of either form stay in sync. Without this,
          // `col.is_visible` would remain stale and downstream code using
          // `col?.is_visible ?? col?.isVisible` would read the old value.
          return col.id === columnId
            ? { ...col, is_visible: nextVisible, isVisible: nextVisible }
            : col;
        });

        return {
          ...oldData,
          data: {
            ...oldData.data,
            result: {
              ...oldData.data.result,
              columnConfig: newColumnOrder,
            },
          },
        };
      },
    );

    //@ts-ignore
    updateDataset({
      dataset_name: currentDataset?.name,
      column_order: columnOrder,
      column_config: columnConfig,
    });
  };

  const onColumnOrderChange = (updatedColumnOrder) => {
    const columnOrder = [];
    const columnConfig = {};
    queryClient.setQueryData(
      getDatasetQueryKey(dataset, 0, [], [], ""),
      (oldData) => {
        const existingColumnOrder = oldData?.data?.result?.columnConfig;
        const colDefMap = existingColumnOrder.reduce((acc, col) => {
          acc[col.id] = col;
          return acc;
        }, {});

        const newColumnOrder = updatedColumnOrder.map((col) => {
          columnOrder.push(col.id);
          columnConfig[col.id] = {
            is_visible: col.isVisible,
            is_frozen: col.pinned,
          };
          return colDefMap[col.id];
        });

        return {
          ...oldData,
          data: {
            ...oldData.data,
            result: {
              ...oldData.data.result,
              columnConfig: newColumnOrder,
            },
          },
        };
      },
    );
    //@ts-ignore
    updateDataset({
      dataset_name: currentDataset?.name,
      column_order: columnOrder,
      column_config: columnConfig,
    });
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: theme.spacing(2) }}>
      {!hideSearch && (
        <>
          <FormSearchField
            size="small"
            placeholder="Search"
            searchQuery={search}
            onChange={(e) => setSearchExtended(e.target.value)}
            disabled={!isData || (isSyntheticDataset && !processingComplete)}
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
                    onClick={() => setSearchExtended("")}
                    sx={{ color: "text.secondary", cursor: "pointer" }}
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
          <Divider
            orientation="vertical"
            flexItem
            sx={{ my: theme.spacing(1) }}
          />
        </>
      )}
      {iconActionButtons
        .filter(
          (button) =>
            !(hideFilter && button.icon === "ic_filter") &&
            !(hideColumnView && button.icon === "ic_column") &&
            !(hideRowHeight && button.icon === "ic_height"),
        )
        .map((button, index) => (
          <CustomTooltip
            key={index}
            show={true}
            title={button.tooltip || ""}
            placement="bottom"
            arrow
            size="small"
          >
            <IconButton
              size="small"
              sx={{ color: "text.secondary" }}
              onClick={() => {
                if (button.event) trackEvent(button.event);
                button.action?.();
              }}
              disabled={!isData || (isSyntheticDataset && !processingComplete)}
              ref={button.ref}
              {...(button.icon === "ic_filter"
                ? { "data-develop-filter-anchor": "" }
                : {})}
            >
              {isFilterApplied && button?.icon === "ic_filter" ? (
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
                >
                  <SvgColor
                    src={`/assets/icons/action_buttons/${button.icon}.svg`}
                    sx={iconStyles}
                  />
                </Badge>
              ) : (
                <SvgColor
                  src={`/assets/icons/action_buttons/${button.icon}.svg`}
                  sx={iconStyles}
                />
              )}
            </IconButton>
          </CustomTooltip>
        ))}
      <ColumnResizer
        open={openResizer}
        anchorEl={resizerRef?.current}
        sizeMapping={defaultRowHeightMapping}
        onSelect={handleHeightSelect}
        onClose={() => setOpenResizer(false)}
        setCellHeight={setCellHeight}
      ></ColumnResizer>
      <ColumnDropdown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={columns}
        onColumnVisibilityChange={onColumnVisibilityChange}
        setColumns={onColumnOrderChange}
        defaultGrouping="Data columns"
      />
    </Box>
  );
};

export default DevelopBarLeftSection;

DevelopBarLeftSection.propTypes = {
  resizerRef: PropTypes.object,
  hideFilter: PropTypes.bool,
  hideColumnView: PropTypes.bool,
  hideSearch: PropTypes.bool,
  hideRowHeight: PropTypes.bool,
};
