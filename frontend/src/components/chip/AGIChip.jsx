import { Chip, styled } from "@mui/material";

export const AGIChip = styled(Chip)(({ theme }) => ({
  backgroundColor: theme.palette.action.hover,
  color: theme.palette.primary.main,
  "&:hover": {
    backgroundColor: theme.palette.action.selected,
    borderColor: theme.palette.action.selected,
  },
}));
