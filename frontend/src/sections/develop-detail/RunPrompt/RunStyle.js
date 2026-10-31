import { Box, RadioGroup, styled, Tab, Tabs, Typography } from "@mui/material";

export const RunBtnWrap = styled(Box)(({ theme }) => ({
  backgroundColor: theme.palette.background.paper + "E5",
  padding: "3px 5px",
  borderRadius: "12px",
  position: "relative",
  overflow: "hidden",
  cursor: "pointer",
  "&:before": {
    backgroundColor: "primary.lighter",
    filter: "blur(40px)",
    content: '""',
    width: "80px",
    height: "80px",
    borderRadius: "50%",
    display: "block",
    position: "absolute",
    top: "-24px",
    left: "-15px",
  },
  "&:after": {
    backgroundColor: "info.lighter",
    filter: "blur(40px)",
    content: '""',
    width: "80px",
    height: "80px",
    borderRadius: "50%",
    display: "block",
    position: "absolute",
    bottom: "-24px",
    right: "-15px",
  },
}));

export const RunWrapper = styled(Box)(({ theme }) => ({
  backgroundColor: theme.palette.background.default,
  width: "480px",
  borderRadius: "12px",
  margin: "0 auto",
}));

export const RunHeader = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
  padding: "13px 12px 13px 24px",
}));
export const RunTitle = styled(Typography)(({ theme }) => ({
  flex: 1,
  fontSize: "16px",
  fontWeight: theme.typography.fontWeightBold,
  color: theme.palette.text.primary,
}));
export const RunBody = styled(Box)(() => ({
  padding: "16px 24px",
}));
export const RunCta = styled(Box)(() => ({
  display: "flex",
  gap: "12px",
  button: {
    flex: 1,
  },
}));

export const RunList = styled(RadioGroup)(() => ({
  display: "flex",
  gap: "8px",
  marginBottom: "40px",
}));
export const RunItem = styled(Box)(() => ({
  display: "flex",
  gap: "10px",
}));

export const PromptWrap = styled(Box)(({ theme }) => ({
  position: "absolute",
  top: "0",
  right: "calc(100% - 9px)",
  width: "525px",
  height: "100%",
  backgroundColor: theme.palette.background.paper,
  // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
  boxShadow: "-40px 40px 80px -8px rgba(145, 158, 171, 0.24)",
}));
export const PromptWrapTtl = styled(Typography)(({ theme }) => ({
  fontWeight: theme.typography.fontWeightBold,
  padding: "16px 20px 12px",
  marginBottom: "12px",
}));
export const CustomTabs = styled(Tabs)(({ theme }) => ({
  marginBottom: "19px",
  "& .MuiTabs-indicator": {
    backgroundColor: theme.palette.primary.main,
  },
}));
export const CustomTab = styled(Tab)(({ theme }) => ({
  color: theme.palette.primary.main,
  "&:not(.Mui-selected)": {
    color: theme.palette.text.disabled,
  },
}));
export const PromptItem = styled(Box)(({ theme }) => ({
  border: "2px solid var(--border-light)",
  borderRadius: "8px",
  padding: "12px 16px",
  minHeight: "72px",
  color: theme.palette.text.primary,
  fontSize: "13px",
  fontWeight: theme.typography.fontWeightRegular,
  lineHeight: 1.6,
}));
