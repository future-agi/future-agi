// AnnotationsStyle.js
import { styled, Switch, alpha } from "@mui/material";
import { Box } from "@mui/system";
// import { grey } from "src/theme/palette";

export const ModalBoxConatiner = styled(Box)(({ theme }) => ({
  backgroundColor: theme.palette.background.paper,
  border: `1px solid ${theme.palette.divider}`,
  padding: "5px",
  boxShadow: `0px 0px 10px ${alpha(theme.palette.common.black, 0.1)}`,
  zIndex: 1000,
  // height: "300px",
  width: "250px",
  // transform: "translateY(calc(50% - 20px))",
  borderRadius: "8px",
}));

export const StyledBox = styled(Box)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  border: ".2px solid gray",
  borderColor: theme.palette.text.disabled,
  borderRadius: "8px",
  height: "35px",
  "&:hover": {
    backgroundColor: theme.palette.action.hover,
  },
}));

export const WrapperBox = styled(Box)(({ theme }) => ({
  width: "100%",
  borderRadius: "12px",
  backgroundColor: theme.palette.background.paper,
  position: "absolute",
  zIndex: 2,
  overflow: "hidden",
  boxShadow: `-20px 20px 40px -4px ${alpha(theme.palette.grey[500], 0.24)}`,
  padding: "4px",
}));

export const AntSwitch = styled(Switch)(({ theme }) => ({
  width: 28,
  height: 16,
  padding: 0,
  display: "flex",
  "&:active": {
    "& .MuiSwitch-thumb": {
      width: 15,
    },
    "& .MuiSwitch-switchBase.Mui-checked": {
      transform: "translateX(9px)",
    },
  },
  "& .MuiSwitch-switchBase": {
    left: 2,
    padding: 2,
    "&.Mui-checked": {
      transform: "translateX(8px)",
      color: theme.palette.common.white,
      "& + .MuiSwitch-track": {
        opacity: 1,
        backgroundColor: theme.palette.info.main,
        ...theme.applyStyles("dark", {
          backgroundColor: theme.palette.info.dark,
        }),
      },
    },
  },
  "& .MuiSwitch-thumb": {
    boxShadow: `0 2px 4px 0 ${alpha(theme.palette.common.black, 0.2)}`,
    width: 12,
    height: 12,
    borderRadius: 6,
    transition: theme.transitions.create(["width"], {
      duration: 200,
    }),
  },
  "& .MuiSwitch-track": {
    borderRadius: 16 / 2,
    opacity: 1,
    backgroundColor: theme.palette.text.disabled,
    boxSizing: "border-box",
  },
}));
