import {
  Box,
  Button,
  styled,
  Tab,
  tabClasses,
  Tabs,
  Typography,
} from "@mui/material";
import { typography } from "src/theme/typography";

const TabWrapper = styled(Box)(({ theme }) => ({
  display: "inline-flex",
  border: `1px solid ${theme.palette.background.neutral}`,
  padding: "4px",
  borderRadius: "8px",
  marginBottom: "19px",
}));
const CustomTabs = styled(Tabs)(() => ({
  minHeight: "29px",
}));
const CustomTab = styled(Tab)(({ theme }) => ({
  fontFamily: typography.fontFamily,
  paddingLeft: "12px",
  paddingRight: "12px",
  minHeight: "29px",
  ...typography.subtitle2,
  ["&:not(:last-of-type)"]: {
    marginRight: "4px",
  },
  [`&:not(.${tabClasses.selected})`]: {
    color: theme.palette.text.secondary,
  },
}));

const CustomLabel = styled(Typography)(({ theme }) => ({
  fontSize: "12px",
  fontWeight: typography.fontWeightBold,
  color: theme.palette.text.disabled,
  paddingLeft: "5px",
  marginBottom: "20px",
}));

const CustomTabButton = styled(Button)(({ theme }) => ({
  width: "100%",
  fontSize: "13px",
  fontWeight: typography.fontWeightRegular,
  justifyContent: "flex-start",
  color: theme.palette.text.primary,
  padding: "11px 12px",
  ["&:not(:last-child)"]: {
    marginBottom: "12px",
  },
  "&.active": {
    fontWeight: typography.fontWeightSemiBold,
    backgroundColor: `${theme.palette.primary.main}14`,
    color: theme.palette.primary.main,
  },
}));

const HeadingH6 = styled(Typography)(({ theme }) => ({
  fontSize: "16px !important",
  fontWeight: typography.fontWeightSemiBold,
  color: theme.palette.text.primary,
  textTransform: "capitalize",
  lineHeight: 1.6,
  marginBottom: "4px",
}));

const SubHeading = styled(Typography)(({ theme }) => ({
  fontSize: "13px !important",
  fontWeight: typography.fontWeightRegular,
  color: theme.palette.text.secondary,
  marginBottom: "24px",
}));

const PropmtTitle = styled(Typography)(({ theme }) => ({
  fontSize: "13px !important",
  fontFamily: typography.fontFamily,
  fontWeight: typography.fontWeightBold,
  color: theme.palette.text.secondary,
  textTransform: "capitalize",
  lineHeight: 1.3,
  marginBottom: "24px",
}));

const CustomText = styled(Typography)(({ theme }) => ({
  border: `1px solid ${theme.palette.divider}`,
  borderTop: 0,
  color: theme.palette.text.primary,
  fontWeight: typography.fontWeightRegular,
  fontSize: "12px",
  marginTop: "-2px",
  padding: "15px 25px",
}));

export {
  TabWrapper,
  CustomTabs,
  CustomTab,
  CustomLabel,
  CustomTabButton,
  PropmtTitle,
  HeadingH6,
  SubHeading,
  CustomText,
};
