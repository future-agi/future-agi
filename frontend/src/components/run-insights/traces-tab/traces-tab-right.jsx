import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";
import { FilterTrigger } from "src/components/filter";
import { OutlinedButton } from "src/sections/project-detail/ProjectDetailComponents";

const TracesTabRightSection = React.forwardRef(
  (
    { setOpenColumnConfigure, setOpenFilter, filterOpen, isFilterApplied },
    columnConfigureRef,
  ) => {
    return (
      <Box
        sx={{
          display: "flex",
          gap: (theme) => theme.spacing(1.25),
          alignItems: "center",
        }}
      >
        <FilterTrigger
          count={typeof isFilterApplied === "number" ? isFilterApplied : 0}
          open={Boolean(filterOpen)}
          onClick={() => setOpenFilter((v) => !v)}
        />
        <OutlinedButton
          startIcon={
            <SvgColor src="/assets/icons/action_buttons/ic_column.svg" />
          }
          variant="outlined"
          ref={columnConfigureRef}
          sx={{
            // color: "text.disabled",
            typography: "s1",
            // paddingX:(theme)=>theme.spacing(3),
            // paddingY:(theme)=>theme.spacing(1)
          }}
          onClick={() => setOpenColumnConfigure(true)}
        >
          Columns
        </OutlinedButton>
      </Box>
    );
  },
);

TracesTabRightSection.displayName = "TracesTabRightSection";

TracesTabRightSection.propTypes = {
  setOpenColumnConfigure: PropTypes.func,
  setOpenFilter: PropTypes.func,
  filterOpen: PropTypes.bool,
  isFilterApplied: PropTypes.oneOfType([PropTypes.bool, PropTypes.number]),
};

export default TracesTabRightSection;
