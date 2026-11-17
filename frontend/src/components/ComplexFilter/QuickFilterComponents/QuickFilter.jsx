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

// The funnel is a tooltip, so its popper stacks at zIndex.tooltip, above side
// drawers. A grid on the page passes `belowDrawers` so the funnel stacks one
// level under drawers instead: an open drawer (the trace drawer is a
// persistent drawer at zIndex.drawer) then covers a hovered cell's funnel like
// the rest of the page. A grid inside a drawer or dialog leaves it off, or its
// own panel would cover the funnel.
const BELOW_DRAWERS_POPPER_SX = {
  zIndex: (theme) => theme.zIndex.drawer - 1,
};

const QuickFilter = ({
  onClick,
  children,
  show = true,
  belowDrawers = false,
}) => {
  if (!show) return <>{children}</>;

  return (
    <CellActionToolTip
      title={<FilterButtonContent onClick={onClick} />}
      placement="top-end"
      slotProps={{
        popper: {
          ...(belowDrawers ? { sx: BELOW_DRAWERS_POPPER_SX } : {}),
          modifiers: [
            {
              name: "offset",
              options: {
                offset: [10, -43],
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
  belowDrawers: PropTypes.bool,
};

export default QuickFilter;
