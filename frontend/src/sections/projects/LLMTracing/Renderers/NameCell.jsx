import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import QuickFilter from "src/components/ComplexFilter/QuickFilterComponents/QuickFilter";

const NameCell = ({ value, colDef, applyQuickFilters }) => {
  return (
    <QuickFilter
      onClick={() =>
        applyQuickFilters({
          col: colDef?.context?.sourceColumn,
          value,
          filterAnchor: {},
        })
      }
    >
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          fontWeight: 400,
          color: "text.primary",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          width: "100%",
          px: 1.5,
          height: "100%",
          display: "flex",
          alignItems: "center",
        }}
      >
        {value}
      </Typography>
    </QuickFilter>
  );
};

NameCell.propTypes = {
  value: PropTypes.any,
  colDef: PropTypes.object,
  data: PropTypes.object,
  applyQuickFilters: PropTypes.func,
};

export default React.memo(NameCell);
