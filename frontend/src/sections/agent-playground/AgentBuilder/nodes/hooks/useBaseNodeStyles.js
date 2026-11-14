import { useMemo } from "react";
import { useTheme } from "@mui/material";
import { statusColors } from "src/utils/runStatus";

export default function useBaseNodeStyles({
  selected,
  hasValidationError,
  isRunning,
  isCompleted,
  isError,
  preview,
  nodeHeight,
}) {
  const theme = useTheme();

  const borderColor = useMemo(() => {
    if (hasValidationError || isError)
      return statusColors(theme, "danger").solid;
    if (isCompleted) return statusColors(theme, "success").solid;
    if (selected) return statusColors(theme, "info").solid;
    return theme.palette.border?.default ?? theme.palette.divider;
  }, [hasValidationError, isError, isCompleted, selected, theme]);

  const borderStyle = isRunning ? "dashed" : "solid";

  const backgroundColor = useMemo(() => {
    if (hasValidationError || isError) return statusColors(theme, "danger").bg;
    if (isCompleted) return statusColors(theme, "success").bg;
    return theme.palette.background.paper;
  }, [hasValidationError, isError, isCompleted, theme]);

  const boxSx = useMemo(
    () => ({
      minWidth: 200,
      borderRadius: "4px",
      minHeight: nodeHeight,
      borderStyle: isRunning ? "none" : borderStyle,
      borderWidth: isRunning ? 0 : "1px",
      borderColor,
      backgroundColor,
      position: "relative",
      py: isRunning ? "9px" : 1,
      px: isRunning ? "13px" : 1.5,
      display: "flex",
      alignItems: "center",
      cursor: preview ? "default" : "pointer",
      overflow: "visible",
      "& .node-delete-btn": {
        opacity: 0,
        pointerEvents: "none",
        transition: "opacity 150ms ease",
      },
      ...(!preview &&
        !isRunning && {
          "&:hover": {
            borderColor: hasValidationError
              ? statusColors(theme, "danger").solid
              : statusColors(theme, "info").solid,
          },
          "&:hover .node-delete-btn": {
            opacity: 1,
            pointerEvents: "auto",
          },
        }),
    }),
    [
      nodeHeight,
      isRunning,
      borderStyle,
      borderColor,
      backgroundColor,
      preview,
      hasValidationError,
      theme,
    ],
  );

  return {
    borderColor,
    borderStyle,
    backgroundColor,
    boxSx,
    theme,
  };
}
