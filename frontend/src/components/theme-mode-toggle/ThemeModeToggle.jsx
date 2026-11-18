import React from "react";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import { useSettingsContext } from "src/components/settings";
import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------

const MODE_CYCLE = ["light", "dark", "system"];

const MODE_CONFIG = {
  light: {
    icon: "solar:sun-linear",
    tooltip: "Light mode",
  },
  dark: {
    icon: "solar:moon-linear",
    tooltip: "Dark mode",
  },
  system: {
    icon: "solar:monitor-linear",
    tooltip: "System mode",
  },
};

export default function ThemeModeToggle() {
  const settings = useSettingsContext();
  const currentMode = settings.themeMode;
  const config = MODE_CONFIG[currentMode] || MODE_CONFIG.system;

  const handleToggle = () => {
    const currentIndex = MODE_CYCLE.indexOf(currentMode);
    const nextIndex = (currentIndex + 1) % MODE_CYCLE.length;
    settings.onUpdate("themeMode", MODE_CYCLE[nextIndex]);
  };

  return (
    <Tooltip title={config.tooltip}>
      <IconButton
        onClick={handleToggle}
        sx={{
          color: "text.secondary",
          "&:hover": {
            bgcolor: "action.hover",
          },
        }}
      >
        <Iconify icon={config.icon} width={20} />
      </IconButton>
    </Tooltip>
  );
}
