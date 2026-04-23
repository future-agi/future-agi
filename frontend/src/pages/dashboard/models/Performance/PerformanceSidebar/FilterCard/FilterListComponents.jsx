import {
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  styled,
} from "@mui/material";

export const FilterList = styled(List)(() => ({
  padding: 0,
}));

export const FilterListItem = styled(ListItem)(() => ({
  padding: 0,
}));

export const FilterListItemButton = styled(ListItemButton)(() => ({
  padding: "6px 8px 6px 8px",
  "&:hover": {
    borderRadius: "6px",
    backgroundColor: "action.hover",
  },
  "&.Mui-selected": {
    borderRadius: "6px",
    backgroundColor: "action.hover",

    "&:hover": {
      borderRadius: "6px",
      backgroundColor: "action.hover",
    },
  },
}));

export const FilterListItemText = styled(ListItemText)(() => ({
  ".MuiListItemText-primary": {
    fontWeight: 400,
    fontSize: "14px",
    color: "text.primary",
  },
}));
