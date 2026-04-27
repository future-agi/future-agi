import { Box, IconButton, Paper } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import FilterSection from "./FilterSection";

const FilterCard = ({
  index,
  removeFilter,
  cloneFilter,
  setFilter,
  filter,
}) => {
  return (
    <Paper
      elevation={2}
      sx={{ padding: "14px", display: "flex", flexDirection: "column" }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <IconButton size="small" onClick={() => cloneFilter(index)}>
            <Iconify
              icon="bxs:duplicate"
              width={16}
              height={16}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
          <IconButton size="small" onClick={() => removeFilter(index)}>
            <Iconify
              icon="solar:trash-bin-trash-bold"
              width={16}
              height={16}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
        </Box>
      </Box>
      <FilterSection setFilter={setFilter} filter={filter} />
    </Paper>
  );
};

FilterCard.propTypes = {
  index: PropTypes.number,
  removeFilter: PropTypes.func,
  cloneFilter: PropTypes.func,
  setFilter: PropTypes.func,
  filter: PropTypes.object,
};

export default FilterCard;
