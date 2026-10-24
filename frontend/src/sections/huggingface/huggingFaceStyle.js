import { Box, styled, Typography } from "@mui/material";

export const FaceWrapper = styled(Box)(({ theme }) => ({
  backgroundColor: theme.palette.background.paper,
  minHeight: "100vh",
  overflow: "auto",
  color: theme.palette.common.black,
  padding: "28px 12px 16px",
}));

export const SearchFieldBox = styled(Box)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: "4px",
  height: "35px",
  maxWidth: "617px",
  margin: "0 auto 17px",
}));

export const FaceRow = styled(Box)(() => ({
  display: "flex",
  flexWrap: "wrap",
  marginLeft: "-8px",
  marginRight: "-8px",
  rowGap: "22px",
}));
export const FaceItem = styled(Box)(({ theme }) => ({
  width: "100%",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: theme.palette.divider,
  borderRadius: "8px",
  padding: "10px 14px",
  minHeight: "100%",
  cursor: "pointer",
  display: "flex",
  flexDirection: "column",
}));
export const FaceInfo = styled(Typography)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  fontSize: "12px",
  gap: "5px",
  fontWeight: theme.typography.fontWeightRegular,
  color: theme.palette.text.disabled,
}));
export const DatasetInfo = styled(Typography)(({ theme }) => ({
  borderRadius: "8px",
  fontSize: "12px",
  padding: "9px 0",
  border: `1px solid ${theme.palette.divider}`,
  marginBottom: "39px",
}));
