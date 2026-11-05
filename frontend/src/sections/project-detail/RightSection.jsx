import { Box, Button, Divider, IconButton, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import { FilterButton } from "src/components/filter";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { Events, trackEvent } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";

const RightSection = React.forwardRef(
  (
    {
      setOpenColumnConfigure,
      setOpenChooseWinnerDrawer,
      selectedRows,
      clearSelection,
      onDelete,
      onExport,
      onCompare,
      setOpenFilter,
      filterOpen,
      totalFilters,
      filterFields,
      filterValue,
      onFilterChange,
    },
    columnConfigureRef,
  ) => {
    if (selectedRows.length > 0) {
      return (
        <Box
          sx={{
            display: "flex",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: (theme) => theme.spacing(1),
            paddingX: (theme) => theme.spacing(2),
            paddingY: (theme) => theme.spacing(0.5),
            alignItems: "center",
            height: "38px",
            gap: (theme) => theme.spacing(2),
          }}
        >
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color="primary.main"
          >
            {selectedRows.length} Selected
          </Typography>
          <Divider
            orientation="vertical"
            flexItem
            sx={{ height: "20px", mt: (theme) => theme.spacing(0.5) }}
          />
          {selectedRows.length > 1 && (
            <CustomTooltip
              show={selectedRows.length > 1}
              title="Select at least 2 rows to compare"
              placement="bottom"
              arrow
            >
              <Box>
                <Button
                  startIcon={
                    <SvgColor
                      src="/icons/datasets/compare_icon.svg"
                      width="20px"
                      sx={{ color: "text.disabled" }}
                    />
                  }
                  size="small"
                  sx={{
                    color: "text.primary",
                    typography: "s1",
                  }}
                  onClick={() => {
                    onCompare();
                    trackEvent(Events.pExperimentCompareRunClicked);
                  }}
                  disabled={selectedRows.length < 2}
                >
                  Compare
                </Button>
              </Box>
            </CustomTooltip>
          )}
          <Button
            startIcon={
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                width="20px"
                sx={{ color: "text.disabled" }}
              />
            }
            size="small"
            sx={{
              color: "text.primary",
              typography: "s1",
            }}
            onClick={onDelete}
          >
            Delete
          </Button>
          <Button
            startIcon={
              <Iconify
                icon="solar:download-minimalistic-linear"
                color="text.disabled"
              />
            }
            size="small"
            sx={{
              color: "text.primary",
              typography: "s1",
            }}
            onClick={onExport}
          >
            Export
          </Button>
          <Divider
            orientation="vertical"
            flexItem
            sx={{ height: "20px", mt: (theme) => theme.spacing(0.5) }}
          />
          <IconButton size="small" onClick={clearSelection}>
            <Iconify
              icon="solar:close-linear"
              sx={{ color: "text.disabled" }}
            />
          </IconButton>
        </Box>
      );
    }

    return (
      <Box
        sx={{
          display: "flex",
          gap: (theme) => theme.spacing(1.25),
          alignItems: "center",
        }}
      >
        <FilterButton
          fields={filterFields}
          value={filterValue}
          onChange={onFilterChange}
          count={totalFilters}
          open={filterOpen}
          onOpenChange={setOpenFilter}
        />
        <Button
          size="small"
          startIcon={
            <SvgColor src="/assets/icons/action_buttons/ic_column.svg" />
          }
          // sx={{ width: '126px', color: 'text.disabled', typography: 's1',}}
          variant="outlined"
          ref={columnConfigureRef}
          onClick={() => setOpenColumnConfigure(true)}
        >
          Columns
        </Button>
        <Button
          size="small"
          startIcon={
            <SvgColor src="/icons/datasets/choose_winner.svg" width="16px" />
          }
          // sx={{ width: '159px', typography: 's1',}}
          color="primary"
          variant="contained"
          onClick={() => setOpenChooseWinnerDrawer(true)}
        >
          Choose Winner
        </Button>
      </Box>
    );
  },
);

RightSection.displayName = "RightSection";

RightSection.propTypes = {
  setOpenColumnConfigure: PropTypes.func,
  setOpenChooseWinnerDrawer: PropTypes.func,
  selectedRows: PropTypes.array,
  clearSelection: PropTypes.func,
  onDelete: PropTypes.func,
  onExport: PropTypes.func,
  onCompare: PropTypes.func,
  setOpenFilter: PropTypes.func,
  filterOpen: PropTypes.bool,
  totalFilters: PropTypes.number,
  filterFields: PropTypes.array,
  filterValue: PropTypes.array,
  onFilterChange: PropTypes.func,
};

export default RightSection;
