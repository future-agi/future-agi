import React from "react";
import Stack from "@mui/material/Stack";
import Badge from "@mui/material/Badge";
import Divider from "@mui/material/Divider";
import Tooltip from "@mui/material/Tooltip";
import { useTheme } from "@mui/material/styles";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import Drawer, { drawerClasses } from "@mui/material/Drawer";

import { paper } from "src/theme/css";

import Iconify from "../../iconify";
import Scrollbar from "../../scrollbar";
import BaseOptions from "./base-option";
import LayoutOptions from "./layout-options";
import PresetsOptions from "./presets-options";
import StretchOptions from "./stretch-options";
import { useSettingsContext } from "../context";
import FullScreenOption from "./fullscreen-option";

// ----------------------------------------------------------------------

export default function SettingsDrawer() {
  const theme = useTheme();

  const settings = useSettingsContext();

  const labelStyles = {
    mb: 1.5,
    color: "text.disabled",
    fontWeight: "fontWeightSemiBold",
  };

  const renderHead = (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{ py: 2, pr: 1, pl: 2.5 }}
    >
      <Typography variant="h6" sx={{ flexGrow: 1 }}>
        Settings
      </Typography>

      <Tooltip title="Reset">
        <IconButton onClick={settings.onReset}>
          <Badge color="error" variant="dot" invisible={!settings.canReset}>
            <Iconify icon="solar:restart-bold" />
          </Badge>
        </IconButton>
      </Tooltip>

      <IconButton onClick={settings.onClose}>
        <Iconify icon="solar:close-linear" />
      </IconButton>
    </Stack>
  );

  const renderMode = (
    <div>
      <Typography variant="caption" component="div" sx={{ ...labelStyles }}>
        Mode
      </Typography>

      <BaseOptions
        value={settings.themeMode}
        onChange={(newValue) => settings.onUpdate("themeMode", newValue)}
        options={["light", "dark", "system"]}
        icons={["sun", "moon", "monitor"]}
      />
    </div>
  );

  const renderContrast = (
    <div>
      <Typography variant="caption" component="div" sx={{ ...labelStyles }}>
        Contrast
      </Typography>

      <BaseOptions
        value={settings.themeContrast}
        onChange={(newValue) => settings.onUpdate("themeContrast", newValue)}
        options={["default", "bold"]}
        icons={["contrast", "contrast_bold"]}
      />
    </div>
  );

  const renderDirection = (
    <div>
      <Typography variant="caption" component="div" sx={{ ...labelStyles }}>
        Direction
      </Typography>

      <BaseOptions
        value={settings.themeDirection}
        onChange={(newValue) => settings.onUpdate("themeDirection", newValue)}
        options={["ltr", "rtl"]}
        icons={["align_left", "align_right"]}
      />
    </div>
  );

  const renderLayout = (
    <div>
      <Typography variant="caption" component="div" sx={{ ...labelStyles }}>
        Layout
      </Typography>

      <LayoutOptions
        value={settings.themeLayout}
        onChange={(newValue) => settings.onUpdate("themeLayout", newValue)}
        options={["vertical", "horizontal", "mini"]}
      />
    </div>
  );

  const renderStretch = (
    <div>
      <Typography
        variant="caption"
        component="div"
        sx={{
          ...labelStyles,
          display: "inline-flex",
          alignItems: "center",
        }}
      >
        Stretch
        <Tooltip title="Only available at large resolutions > 1600px (xl)">
          <Iconify
            icon="solar:info-circle-linear"
            width={16}
            sx={{ ml: 0.5 }}
          />
        </Tooltip>
      </Typography>

      <StretchOptions
        value={settings.themeStretch}
        onChange={() =>
          settings.onUpdate("themeStretch", !settings.themeStretch)
        }
      />
    </div>
  );

  const renderPresets = (
    <div>
      <Typography variant="caption" component="div" sx={{ ...labelStyles }}>
        Presets
      </Typography>

      <PresetsOptions
        value={settings.themeColorPresets}
        onChange={(newValue) =>
          settings.onUpdate("themeColorPresets", newValue)
        }
      />
    </div>
  );

  return (
    <Drawer
      anchor="right"
      open={settings.open}
      onClose={settings.onClose}
      slotProps={{
        backdrop: { invisible: true },
      }}
      sx={{
        [`& .${drawerClasses.paper}`]: {
          ...paper({ theme, bgcolor: theme.palette.background.default }),
          width: 280,
        },
      }}
    >
      {renderHead}

      <Divider sx={{ borderStyle: "dashed" }} />

      <Scrollbar>
        <Stack spacing={3} sx={{ p: 3 }}>
          {renderMode}

          {renderContrast}

          {renderDirection}

          {renderLayout}

          {renderStretch}

          {renderPresets}
        </Stack>
      </Scrollbar>

      <FullScreenOption />
    </Drawer>
  );
}
