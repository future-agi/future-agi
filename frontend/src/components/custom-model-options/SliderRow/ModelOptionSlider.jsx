import React from "react";
import { Slider, styled, Tooltip } from "@mui/material";
import PropTypes from "prop-types";

//  What this does: Instead of MUI Slider's default value label (absolutely positioned <span> trapped inside the overflow
//   container), the slider now uses MUI's Tooltip component which internally uses Popper + createPortal to render at
//   document.body. The tooltip escapes any overflow boundary and will never get clipped regardless of parent container
//   styles.
const PortalValueLabel = ({ children, value, open }) => (
  <Tooltip open={open} title={value} placement="top" arrow>
    {children}
  </Tooltip>
);

PortalValueLabel.propTypes = {
  children: PropTypes.node.isRequired,
  // MUI passes the valueLabelFormat output here, which callers format as text.
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.node]).isRequired,
  open: PropTypes.bool.isRequired,
};

export const ModelOptionSlider = styled((props) => (
  <Slider slots={{ valueLabel: PortalValueLabel }} {...props} />
))(({ theme }) => ({
  color: theme.palette.text.secondary,
  padding: theme.spacing(0),
  "& .MuiSlider-rail": {
    backgroundColor: theme.palette.divider,
    height: theme.spacing(0.5),
  },
  "& .MuiSlider-track": {
    height: theme.spacing(0.5),
    zIndex: 2,
  },
  "& .MuiSlider-thumb": {
    zIndex: 3,
    width: theme.spacing(1.5),
    height: theme.spacing(1.5),
    backgroundColor: theme.palette.divider,
    border: "3px solid var(--bg-paper)",
    boxShadow: "0px 2px 4px rgba(0, 0, 0, 0.1)",
    "&:hover, &.Mui-focusVisible": {
      boxShadow: "0px 3px 6px rgba(0, 0, 0, 0.15)",
    },
  },
  "& .MuiSlider-mark": {
    backgroundColor: theme.palette.action.selected,
    height: theme.spacing(0.5),
    width: theme.spacing(0.25),
  },
}));
