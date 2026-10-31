import { Box, styled, Typography } from "@mui/material";
import { typography } from "src/theme/typography";

export const LabelCard = styled(Box)(({ theme }) => ({
  top: "calc(100% + 8px)",
  left: "0",
  width: "100%",
  borderRadius: "12px",
  backgroundColor: theme.palette.background.paper,
  backdropFilter: "blur(4px)",
  position: "absolute",
  zIndex: 2,
  overflow: "hidden",
  // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
  boxShadow: "-20px 20px 40px -4px rgba(145, 158, 171, 0.24)",
  "&:before": {
    content: '""',
    position: "absolute",
    top: "-16px",
    right: "-16px",
    width: "80px",
    height: "80px",
    backgroundColor: theme.palette.info.lighter,
    filter: "blur(44px)",
  },
  "&:after": {
    content: '""',
    position: "absolute",
    bottom: "-16px",
    left: "-16px",
    width: "80px",
    height: "80px",
    backgroundColor: theme.palette.primary.lighter,
    filter: "blur(44px)",
  },
}));

export const SearchBox = styled(Box)(({ theme }) => ({
  display: "flex",
  alignItems: "center",
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: "8px",
  height: "35px",
}));

export const LabelButton = styled(Typography)(({ theme }) => ({
  borderRadius: "6px",
  color: theme.palette.text.primary,
  cursor: "pointer",
  fontSize: "13px",
  fontWeight: typography.fontWeightRegular,
  lineHeight: 1.6,
  padding: "6px 8px",
  "&.active": {
    fontWeight: typography.fontWeightSemiBold,
    backgroundColor: "action.hover",
  },
  "&:hover": {
    backgroundColor: "action.hover",
  },
  "&:not(:last-child)": {
    marginBottom: "4px",
  },
}));

export const ModalWrap = styled(Box)(({ theme }) => ({
  backgroundColor: theme.palette.background.paper,
  borderRadius: 16,
  width: "100%",
  maxWidth: 709,
  overflow: "hidden",
}));

export const ModalHeader = styled(Box)(() => ({
  alignItems: "center",
  display: "flex",
  justifyContent: "space-between",
  padding: "13px 24px",
  marginBottom: 6,
}));

export const ModalTitle = styled(Typography)(({ theme }) => ({
  fontSize: 16,
  fontWeight: theme.typography.fontWeightBold,
}));

export const ModalBody = styled(Box)(() => ({
  padding: "0 24px 24px",
}));

export const PreviewWrap = styled(Box)(({ theme }) => ({
  borderRadius: "8px",
  border: `1px solid ${theme.palette.background.neutral}`,
  overflow: "hidden",
  marginTop: "29px",
}));
