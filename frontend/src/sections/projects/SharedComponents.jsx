import { styled } from "@mui/material/styles";
import { Select, MenuItem, IconButton } from "@mui/material";

export const StyledIntervalSelect = styled(Select)(({ theme }) => ({
  borderRadius: theme.spacing(0.5),
  height: "30px",
  borderColor: theme.palette.divider,
  backgroundColor: theme.palette.background.paper,
  "& .MuiOutlinedInput-notchedOutline": {
    borderColor: theme.palette.divider,
  },
  "&:hover .MuiOutlinedInput-notchedOutline": {
    borderColor: theme.palette.divider,
  },
  "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
    borderColor: theme.palette.divider,
  },
}));

export const StyledIntervalMenuItem = styled(MenuItem)(({ theme }) => ({
  px: 1.5,
  minWidth: 0,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  height: "28px",
  "&.Mui-selected": {
    backgroundColor: theme.palette.action.hover,
    fontWeight: 600,
    color: theme.palette.primary.main,
    "&:hover": {
      backgroundColor: theme.palette.action.hover,
    },
  },
  "&:hover": {
    backgroundColor: "transparent",
    transition:
      "background-color 0.2s ease, font-weight 0.2s ease, color 0.2s ease",
  },
  "&.Mui-disabled": {
    opacity: 0.5,
  },
}));

export const ObserveIconButton = styled(IconButton)(({ theme }) => ({
  borderRadius: 6,
  border: `1px solid ${theme.palette.border?.default || theme.palette.divider}`,
  padding: theme.spacing(0.625),
  color: "text.primary",
}));
