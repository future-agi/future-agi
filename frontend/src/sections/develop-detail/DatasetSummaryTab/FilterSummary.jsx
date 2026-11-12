import { Box, IconButton, Typography, useTheme } from "@mui/material";
import React, { useRef, useState } from "react";
import SvgColor from "src/components/svg-color/svg-color";
import FilterItems from "./FilterItems";
import PropTypes from "prop-types";

const FilterSummary = ({
  columnLists,
  handleApplyFilter,
  selectedColumn,
  setSelectedColumn,
  appliedFilter,
}) => {
  const theme = useTheme();
  const ref = useRef(null);
  const [openFilter, setOpenFilter] = useState(false);

  const handleFilter = () => {
    setOpenFilter(true);
  };

  const onCancled = () => {
    setSelectedColumn([...appliedFilter]);
  };

  return (
    <Box>
      <FilterItems
        ref={ref}
        open={openFilter}
        onClose={() => setOpenFilter(false)}
        columnLists={columnLists}
        handleApplyFilter={handleApplyFilter}
        selectedColumn={selectedColumn}
        setSelectedColumn={setSelectedColumn}
        onCancled={onCancled}
      />
      <IconButton
        ref={ref}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          gap: theme.spacing(1),
        }}
        disabled={false}
        onClick={handleFilter}
      >
        <SvgColor
          src={`/assets/icons/action_buttons/ic_filter.svg`}
          sx={{
            width: 16,
            height: 16,
            color: "text.primary",
          }}
        />
        <Typography
          typography="s2"
          fontWeight={"fontWeightSemiBold"}
          color={"text.primary"}
        >
          Filter {appliedFilter.length > 0 && `(${appliedFilter.length})`}
        </Typography>
      </IconButton>
    </Box>
  );
};

export default FilterSummary;

FilterSummary.propTypes = {
  columnLists: PropTypes.array,
  handleApplyFilter: PropTypes.func,
  selectedColumn: PropTypes.array,
  setSelectedColumn: PropTypes.func,
  appliedFilter: PropTypes.array,
};
