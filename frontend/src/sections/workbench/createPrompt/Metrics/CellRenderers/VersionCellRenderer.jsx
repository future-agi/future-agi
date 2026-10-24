import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import QuickFilter from "src/components/ComplexFilter/QuickFilterComponents/QuickFilter";
import VersionStyle from "../../promptActions/VersionStyle";

const VersionCellRenderer = (params) => {
  const { value, applyQuickFilters, column } = params;

  if (!value) {
    return (
      <Box
        sx={{
          paddingX: 1,
          height: "100%",
          alignItems: "center",
          display: "flex",
        }}
      >
        -
      </Box>
    );
  }

  const handleFilterClick = (e) => {
    if (!applyQuickFilters) return;
    applyQuickFilters({
      col: column,
      value: params.value,
      filterAnchor: {
        top: e.clientY,
        left: e.clientX,
      },
    });
  };

  return (
    <QuickFilter onClick={handleFilterClick}>
      <Box
        sx={{
          paddingX: 1.5,
          height: "100%",
          alignItems: "center",
          display: "flex",
          width: "100%",
        }}
      >
        <Typography
          fontSize="13px"
          fontWeight={400}
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          <VersionStyle text={value} />
        </Typography>
      </Box>
    </QuickFilter>
  );
};

VersionCellRenderer.propTypes = {
  value: PropTypes.string,
  applyQuickFilters: PropTypes.func,
  colDef: PropTypes.object,
};

export default VersionCellRenderer;
