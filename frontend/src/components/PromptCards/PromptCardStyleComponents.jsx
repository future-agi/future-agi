import { Box, Button, Checkbox, styled, useTheme } from "@mui/material";
import SVGColor from "src/components/svg-color";

export const PromptCardWrapper = styled(Box)(({ theme }) => ({
  // border: "1px solid",
  // borderColor: theme.palette.divider,
  backgroundColor: theme.palette.background.paper,
  borderRadius: "4px",
  display: "flex",
  flexDirection: "column",
  gap: "8px",
  // padding: "12px 16px",
  position: "relative",
}));

export const GeneratePromptButton = styled(Button, {
  shouldForwardProp: (prop) => prop !== "borderRadius" && prop !== "padding",
})(({ borderRadius = "4px", padding = "4px 8px", height = "24px", theme }) => {
  const gradientColor =
    theme.palette.mode === "light"
      ? "linear-gradient(to right, var(--primary-main), #CF6BE8)"
      : "linear-gradient(to right, #FFFFFF, #E6E6E7)";

  return {
    position: "relative",
    background: "transparent",
    fontSize: "12px",
    lineHeight: "16px",
    fontWeight: "600",
    padding,
    color: "transparent", // default for gradient text
    height,
    minWidth: "32px",
    "& .MuiButton-startIcon": {
      marginRight: 0,
    },
    "&::before": {
      content: '""',
      position: "absolute",
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      borderRadius,
      padding: "1px",
      background: gradientColor,
      WebkitMask:
        "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
      WebkitMaskComposite: "xor",
      maskComposite: "exclude",
    },
    backgroundImage: gradientColor,
    WebkitBackgroundClip: "text",
    backgroundClip: "text",

    "&:hover": {
      "&::before": {
        background: gradientColor,
      },
      backgroundImage: gradientColor,
      WebkitBackgroundClip: "text",
      backgroundClip: "text",
    },

    "&.Mui-disabled": {
      color: theme.palette.text.disabled,
      backgroundImage: "none",
      WebkitBackgroundClip: "unset",
      backgroundClip: "unset",
      "& .MuiButton-startIcon": {
        "& .svg-color": {
          background: `${theme.palette.text.disabled} !important`,
        },
      },
      "&::before": {
        background: theme.palette.action.disabled,
        WebkitMask:
          "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
        maskComposite: "exclude",
      },
    },
  };
});

export const GeneratePromptButtonIcon = () => {
  const theme = useTheme();
  const gradientColor =
    theme.palette.mode === "light"
      ? "linear-gradient(to right, var(--primary-main), #CF6BE8)"
      : "linear-gradient(to right, #FFFFFF, #E6E6E7)";

  return (
    <SVGColor
      src="/assets/icons/components/ic_generate_prompt.svg"
      sx={{
        background: gradientColor,
        height: "13px",
        width: "14px",
      }}
    />
  );
};

export const SyncButton = styled(Box)(({ theme }) => ({
  border: "1px solid",
  borderColor: theme.palette.divider,
  borderRadius: "4px",
  padding: "3px 8px",
  fontSize: theme.typography.s3.fontSize,
  lineHeight: theme.typography.s3.lineHeight,
  fontWeight: theme.typography.fontWeightMedium,
  backgroundColor: theme.palette.background.paper,
  display: "flex",
  alignItems: "center",
  gap: "4px",
  cursor: "pointer",
  color: theme.palette.text.primary,
  userSelect: "none",
}));

export const SyncButtonCheckbox = styled(Checkbox)(({ theme }) => ({
  padding: 0,
  width: "16px",
  height: "16px",
  color: theme.palette.action.hover,
  "&.Mui-checked": {
    color: theme.palette.primary.light,
  },
}));
