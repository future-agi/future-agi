import React, { useMemo, useEffect } from "react";
import merge from "lodash/merge";
import PropTypes from "prop-types";
import CssBaseline from "@mui/material/CssBaseline";
import {
  createTheme,
  ThemeProvider as MuiThemeProvider,
} from "@mui/material/styles";
import { useSettingsContext } from "src/components/settings";
import { useSystemThemeMode } from "src/hooks/use-system-theme-mode";

// system
import { palette } from "./palette";
import { shadows } from "./shadows";
import { typography } from "./typography";
// options
import RTL from "./options/right-to-left";
import { customShadows } from "./custom-shadows";
import { componentsOverrides } from "./overrides";
import { createPresets } from "./options/presets";
import { createContrast } from "./options/contrast";

// ----------------------------------------------------------------------

export default function ThemeProvider({ children }) {
  const settings = useSettingsContext();
  const systemMode = useSystemThemeMode();

  // Resolve "system" to the effective "light" or "dark"
  const effectiveMode =
    settings.themeMode === "system" ? systemMode : settings.themeMode;

  const presets = createPresets(settings.themeColorPresets);

  const contrast = createContrast(settings.themeContrast, effectiveMode);

  // Sync data-theme attribute and CSS variables from palette on <body>
  useEffect(() => {
    const body = document.body;
    body.setAttribute("data-theme", effectiveMode);
  }, [effectiveMode]);

  const memoizedValue = useMemo(
    () => ({
      palette: {
        ...palette(effectiveMode),
      },
      customShadows: {
        ...customShadows(effectiveMode),
        ...presets.customShadows,
      },
      direction: settings.themeDirection,
      shadows: shadows(effectiveMode),
      shape: { borderRadius: 8 },
      typography,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      effectiveMode,
      settings.themeDirection,
      presets.palette,
      presets.customShadows,
      contrast.palette,
    ],
  );

  const theme = createTheme(memoizedValue);

  theme.components = merge(componentsOverrides(theme), contrast.components);

  // Generate CSS variables from the resolved palette — single source of truth
  useEffect(() => {
    const s = document.body.style;
    const p = theme.palette;
    s.setProperty("--primary-main", p.primary.main);
    s.setProperty("--bg-default", p.background.default);
    s.setProperty("--bg-paper", p.background.paper);
    s.setProperty("--bg-neutral", p.background.neutral);
    s.setProperty("--bg-subtle", p.background.subtle);
    s.setProperty("--bg-elevated", p.background.neutral);
    s.setProperty("--input-surface", p.background.paper);
    s.setProperty("--text-primary", p.text.primary);
    s.setProperty("--text-secondary", p.text.secondary);
    s.setProperty("--text-muted", p.text.muted);
    s.setProperty("--text-disabled", p.text.disabled);
    s.setProperty("--border-default", p.border?.default || p.divider);
    s.setProperty("--border-hover", p.border?.hover || p.divider);
    s.setProperty("--border-active", p.border?.active || p.divider);
    s.setProperty("--border-light", p.border?.default || "#e6e6e6");
    s.setProperty("--surface-menu", p.background.neutral);
    s.setProperty("--surface-header", p.background.neutral);
    s.setProperty("--surface-row-active", p.action.selected);
    s.setProperty("--surface-row-hover", p.action.hover);
    s.setProperty("--bg-input", p.background.neutral);
    s.setProperty("--syntax-string", p.syntax.string);
    s.setProperty("--syntax-number", p.syntax.number);
    s.setProperty("--syntax-boolean", p.syntax.boolean);
  }, [theme.palette]);

  return (
    <MuiThemeProvider theme={theme}>
      <RTL themeDirection={settings.themeDirection}>
        <CssBaseline />
        {children}
      </RTL>
    </MuiThemeProvider>
  );
}

ThemeProvider.propTypes = {
  children: PropTypes.node,
};
