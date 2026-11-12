import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import QuickFilter from "src/components/ComplexFilter/QuickFilterComponents/QuickFilter";

// The hover funnel (QuickFilter) is drawn inside the right end of the cell: a
// 24px button that sits 10px to 34px in from the edge. The name stops 40px
// short of the edge, so the funnel never covers the end of the name.
const FUNNEL_GUTTER = "40px";

const NameCell = ({
  value,
  colDef,
  applyQuickFilters,
  quickFilterBelowDrawers,
}) => {
  return (
    <QuickFilter
      belowDrawers={quickFilterBelowDrawers}
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
          width: "100%",
          pl: 1.5,
          pr: FUNNEL_GUTTER,
          height: "100%",
          display: "flex",
          alignItems: "center",
        }}
      >
        {/* A flex container draws no ellipsis for its own text, so the name
            sits in a block that ends in "…" like the Input/Output cells. */}
        <Box
          component="span"
          sx={{
            display: "block",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {value}
        </Box>
      </Typography>
    </QuickFilter>
  );
};

NameCell.propTypes = {
  value: PropTypes.any,
  colDef: PropTypes.object,
  data: PropTypes.object,
  applyQuickFilters: PropTypes.func,
  quickFilterBelowDrawers: PropTypes.bool,
};

export default React.memo(NameCell);
