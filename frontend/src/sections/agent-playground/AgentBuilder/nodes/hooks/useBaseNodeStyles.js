import { useMemo } from "react";
import { alpha, useTheme } from "@mui/material";

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
  const isDark = theme.palette.mode === "dark";

  const borderColor = useMemo(() => {
    if (hasValidationError || isError) return theme.palette.red[500];
    if (isCompleted) return theme.palette.green[500];
    if (selected) return theme.palette.blue[500];
    return isDark ? theme.palette.black[400] : theme.palette.black[200];
  }, [hasValidationError, isError, isCompleted, selected, isDark, theme]);

  const borderStyle = isRunning ? "dashed" : "solid";

  const backgroundColor = useMemo(() => {
    if (hasValidationError || isError)
      return isDark
        ? alpha(theme.palette.red[700], 0.3)
        : theme.palette.red[50];
    if (isCompleted)
      return isDark
        ? alpha(theme.palette.green[500], 0.3)
        : theme.palette.green[50];
    return theme.palette.background.paper;
  }, [hasValidationError, isError, isCompleted, isDark, theme]);

  const boxSx = useMemo(
    () => ({
      minWidth: 200,
      borderRadius: 0.5,
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
              ? theme.palette.red[500]
              : theme.palette.blue[500],
          },
          "&:hover .node-delete-btn, &:focus-within .node-delete-btn": {
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
