import React from "react";
import { Slider, styled } from "@mui/material";

export const TaskOptionsSlider = styled(Slider)(({ theme }) => ({
  color: theme.palette.primary.main,
  "& .MuiSlider-rail": {
    backgroundColor: theme.palette.divider,
    height: 10,
  },
  "& .MuiSlider-track": {
    backgroundColor: theme.palette.text.secondary,
    height: 8,
    borderColor: theme.palette.text.secondary,
  },
  "& .MuiSlider-mark": {
    height: "6px",
    width: "1px",
    color: "text.disabled",
  },
  "& .MuiSlider-thumb": {
    width: 20,
    height: 20,
    backgroundColor: theme.palette.divider,
    border: "3px solid var(--bg-paper)",
  },
}));
