import { IconButton } from "@mui/material";
import React from "react";
import SvgColor from "../../svg-color";
import PropTypes from "prop-types";
import CellActionToolTip from "src/components/CellActionToolTip/CellActionToolTip";

const FilterButtonContent = ({ onClick }) => {
  return (
    <IconButton
      size="small"
      onClick={onClick}
      aria-label="Filter by this value"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <SvgColor
        src={`/assets/icons/components/ic_filter.svg`}
        sx={{ color: "text.primary", width: "12px", height: "12px" }}
      />
    </IconButton>
  );
};

FilterButtonContent.propTypes = {
  onClick: PropTypes.func,
};

const QuickFilter = ({ onClick, children, show = true }) => {
  if (!show) return <>{children}</>;

  return (
    <CellActionToolTip
      title={<FilterButtonContent onClick={onClick} />}
      placement="top-end"
      slotProps={{
        popper: {
          modifiers: [
            {
              name: "offset",
              options: {
                offset: [10, -50],
              },
            },
          ],
        },
      }}
    >
      {children}
    </CellActionToolTip>
  );
};

QuickFilter.propTypes = {
  onClick: PropTypes.func,
  children: PropTypes.node,
  show: PropTypes.bool,
};

export default QuickFilter;
