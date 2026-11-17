import { Box, MenuItem, styled } from "@mui/material";
import { typography } from "src/theme/typography";

export const SearchFieldBox = styled(Box)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: "6px",
  height: "35px",
}));

export const LabelButton = styled(MenuItem)(({ theme }) => ({
  color: theme.palette.primary.main,
  cursor: "pointer",
  fontSize: "13px",
  fontWeight: typography.fontWeightSemiBold,
  lineHeight: 1.6,
  display: "flex",
  alignItems: "center",
  borderTop: 1,
  borderTopStyle: "solid",
  borderTopColor: "divider",
  padding: "6px 8px",
  borderRadius: 0,
  marginTop: "10px",
}));
