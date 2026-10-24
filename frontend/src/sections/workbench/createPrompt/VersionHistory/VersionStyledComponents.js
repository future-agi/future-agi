import { Box, Button, Chip, styled } from "@mui/material";

export const DefaultChip = styled(Chip)(({ theme }) => ({
  backgroundColor: theme.palette.primary.dark,
  color: theme.palette.primary.contrastText,
  borderRadius: "100px",
  fontSize: "12px",
  fontWeight: 500,
  height: "22px",

  "& .MuiChip-label": {
    padding: "2px 12px 2px 12px",
  },
}));

export const VersionCardWrapper = styled(Box)(({ theme }) => ({
  border: "1px solid",
  borderColor: theme.palette.divider,
  borderRadius: theme.spacing(1),
  padding: theme.spacing(2),
  gap: theme.spacing(0.5),
  display: "flex",
  flexDirection: "column",
  "&:hover": {
    borderColor: theme.palette.primary.light,
    backgroundColor: theme.palette.action.hover,
  },

  cursor: "pointer",
}));

export const RestoreButton = styled(Button)(({ theme }) => ({
  display: "none",
  backgroundColor: theme.palette.background.paper,
  fontWeight: theme.typography.fontWeightMedium,
  borderRadius: theme.spacing(1),
  border: "1px solid",
  borderColor: theme.palette.text.disabled,
  padding: "6px 24px",
  width: "90px",
  color: theme.palette.text.primary,
  fontSize: "12px",
  lineHeight: "18px",
  height: "32px",
  "&:hover": {
    backgroundColor: theme.palette.background.paper,
  },
}));

export const CommitBox = styled(Box)(({ theme }) => ({
  border: "1px solid",
  borderColor: theme.palette.divider,
  borderRadius: "4px",
  padding: "12px",
  background: theme.palette.background.default,

  display: "flex",
  flexDirection: "column",
  gap: theme.spacing(0.5),
}));
