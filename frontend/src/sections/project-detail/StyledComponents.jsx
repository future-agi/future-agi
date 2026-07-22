import { Checkbox, styled } from "@mui/material";
import React from "react";

export const LightCheckbox = styled((props) => <Checkbox {...props} />)(
  ({ theme }) => ({
    color: theme.palette.primary.light,
    "&.Mui-checked": { color: theme.palette.primary.light },
  }),
);
