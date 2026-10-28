import { useMemo } from "react";
import { useTheme } from "@mui/material";

// Speaker colours (theme-aware via palette lookup), shared by the transcript
// and the recording's waveform so a role reads the same colour in both.
export const useSpeakerColors = () => {
  const theme = useTheme();
  return useMemo(
    () => ({
      assistant:
        theme.palette.mode === "dark"
          ? theme.palette.primary.light
          : theme.palette.primary.main,
      user: theme.palette.mode === "dark" ? "#FF9933" : "#E9690C",
      system: theme.palette.mode === "dark" ? "#a78bfa" : "#7c3aed",
      tool: theme.palette.mode === "dark" ? "#fbbf24" : "#d97706",
      unknown: theme.palette.text.disabled,
    }),
    [theme],
  );
};
