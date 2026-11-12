import { Box, styled, Typography } from "@mui/material";

export const PromptBox = styled(Box)(() => ({
  flex: 1,
  overflow: "auto",
  padding: "20px",
}));
export const PromptBoxTitle = styled(Typography)(({ theme }) => ({
  color: theme.palette.text.disabled,
  fontSize: "13px",
  fontWeight: theme.typography.fontWeightBold,
}));
