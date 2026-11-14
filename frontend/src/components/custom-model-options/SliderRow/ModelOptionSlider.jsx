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
  value: PropTypes.number.isRequired,
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
  },
  "& .MuiSlider-mark": {
    backgroundColor: theme.palette.action.selected,
    height: theme.spacing(0.5),
    width: theme.spacing(0.25),
  },
}));
