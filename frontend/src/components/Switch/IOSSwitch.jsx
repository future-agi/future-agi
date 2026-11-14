import styled from "@emotion/styled";
import { Switch } from "@mui/material";

export const IOSSwitch = styled((props) => (
  <Switch focusVisibleClassName=".Mui-focusVisible" disableRipple {...props} />
))(({ theme }) => ({
  width: 36,
  height: 20,
  padding: 0,
  display: "flex",
  "& .MuiSwitch-switchBase": {
    padding: 0,
    marginTop: 1,
    transitionDuration: "300ms",
    "&.Mui-checked": {
      transform: "translateX(16px)",
      color: theme.palette.common.white,
      "& + .MuiSwitch-track": {
        backgroundColor: "primary.main",
        opacity: 1,
        border: 0,
      },
      "&.Mui-disabled + .MuiSwitch-track": {
        opacity: 0.5,
      },
    },
    "&.Mui-disabled .MuiSwitch-thumb": {
      color: theme.palette.background.default,
    },
    "&.Mui-disabled + .MuiSwitch-track": {
      opacity: 0.5,
    },
  },
  "& .MuiSwitch-thumb": {
    boxSizing: "border-box",
    width: 17,
    height: 17,
  },
  "& .MuiSwitch-track": {
    borderRadius: 100,
    backgroundColor: "action.hover",
    opacity: 1,
    transition: theme.transitions.create(["background-color"], {
      duration: 200,
    }),
  },
}));
