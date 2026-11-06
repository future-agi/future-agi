import {
  Box,
  Divider,
  IconButton,
  InputAdornment,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import ColumnResizer from "src/components/ColumnResizer/ColumnResizer";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify/iconify";
import SvgColor from "src/components/svg-color/svg-color";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useDebounce } from "src/hooks/use-debounce";
import { FilterTrigger } from "src/components/filter";
import { defaultRowHeightMapping } from "src/utils/constants";
import { Events } from "src/utils/Mixpanel/EventNames";
import { trackEvent } from "src/utils/Mixpanel/mixpanel";

const TableFilterOptions = ({
  columnConfigureRef = {},
  resizerRef = {},
  gridApiRef = {},
  setOpenColumnConfigure = () => {},
  setDevelopFilterOpen = () => {},
  setCellHeight = () => {},
  setSearchQuery = (_value) => {},
  hideFilter = false,
  hideColumnView = false,
  hideSearch = false,
  hideRowHeight = false,
  filterCount = 0,
  filterOpen = false,
  isData,
}) => {
  const theme = useTheme();
  const [searchKey, setSearchKey] = useState("");
  const debouncedSearchText = useDebounce(searchKey.trim(), 300);
  useMemo(() => {
    setSearchQuery(debouncedSearchText);
  }, [debouncedSearchText, setSearchQuery]);
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
  ];
  const iconStyles = {
    width: 16,
    height: 16,
    color: isData ? "text.primary" : "divider",
  };

  const handleHeightSelect = (mappingObject) => {
    const height = mappingObject.height;
    if (gridApiRef && gridApiRef.current) {
      gridApiRef?.current?.api.forEachNode((node) => {
        node.setRowHeight(height);
      });
    }

    gridApiRef?.current?.api.onRowHeightChanged();
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: theme.spacing(2) }}>
      {!hideSearch && (
        <>
          <FormSearchField
            size="small"
            placeholder="Search"
            searchQuery={searchKey}
            onChange={(e) => setSearchKey(e.target.value)}
            disabled={!isData}
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
              endAdornment: searchKey && (
                <InputAdornment position="end">
                  <Iconify
                    icon="solar:close-linear"
                    onClick={() => setSearchKey("")}
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
              sx={{ color: "text.disabled" }}
              onClick={() => {
                if (button.event) trackEvent(button.event);
                button.action?.();
              }}
              disabled={!isData}
              ref={button.ref}
            >
              <SvgColor
                src={`/assets/icons/action_buttons/${button.icon}.svg`}
                sx={iconStyles}
              />
            </IconButton>
          </CustomTooltip>
        ))}
      {!hideFilter && (
        <FilterTrigger
          count={filterCount}
          open={Boolean(filterOpen)}
          disabled={!isData}
          onClick={() => {
            trackEvent(Events.datasetFilterSelected);
            setDevelopFilterOpen((b) => !b);
          }}
        />
      )}
      <ColumnResizer
        open={openResizer}
        anchorEl={resizerRef?.current}
        sizeMapping={defaultRowHeightMapping}
        onSelect={handleHeightSelect}
        onClose={() => setOpenResizer(false)}
        setCellHeight={setCellHeight}
      ></ColumnResizer>
    </Box>
  );
};

export default TableFilterOptions;

TableFilterOptions.propTypes = {
  columnConfigureRef: PropTypes.object,
  resizerRef: PropTypes.object,
  gridApiRef: PropTypes.object,
  setOpenColumnConfigure: PropTypes.func,
  setDevelopFilterOpen: PropTypes.func,
  setCellHeight: PropTypes.func,
  setSearchQuery: PropTypes.func,
  hideFilter: PropTypes.bool,
  hideColumnView: PropTypes.bool,
  hideSearch: PropTypes.bool,
  hideRowHeight: PropTypes.bool,
  filterCount: PropTypes.number,
  filterOpen: PropTypes.bool,
  isData: PropTypes.bool,
};
