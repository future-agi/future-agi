import { Box, ButtonBase, Typography } from "@mui/material";
import SwitcherSubmenu from "./SwitcherSubmenu";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useSettingsContext } from "src/components/settings";

const THEME_OPTIONS = [
  {
    value: "light",
    label: "Light",
    icon: "solar:sun-linear",
  },
  {
    value: "dark",
    label: "Dark",
    icon: "solar:moon-linear",
  },
  {
    value: "system",
    label: "System",
    icon: "solar:monitor-linear",
  },
];

const ThemeSelectorChild = React.forwardRef(({ setOpen }, popperTimeoutRef) => {
  const settings = useSettingsContext();

  return (
    <Box
      onMouseEnter={() => {
        if (popperTimeoutRef.current) {
          clearTimeout(popperTimeoutRef.current);
          popperTimeoutRef.current = null;
        }
        setOpen(true);
      }}
      onMouseLeave={(event) => {
        if (event.currentTarget.contains(document.activeElement)) return;
        popperTimeoutRef.current = setTimeout(() => {
          setOpen(false);
        }, 100);
      }}
      sx={{
        backgroundColor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: (theme) => theme.customShadows?.dropdown,
        p: 0.5,
        minWidth: "160px",
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
      }}
    >
      {THEME_OPTIONS.map((option) => {
        const isActive = settings.themeMode === option.value;

        return (
          <Box
            component={ButtonBase}
            aria-pressed={isActive}
            key={option.value}
            onClick={() => {
              settings.onUpdate("themeMode", option.value);
              setOpen(false);
            }}
            sx={{
              px: 1,
              py: 0.5,
              cursor: "pointer",
              borderRadius: "4px",
              backgroundColor: isActive ? "background.neutral" : "transparent",
              "&:hover, &:focus-visible": {
                backgroundColor: "background.neutral",
              },
              "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <Iconify
              icon={option.icon}
              width={16}
              sx={{ color: "text.primary" }}
            />
            <Typography
              typography="s2_1"
              color="text.primary"
              fontWeight={400}
              sx={{ flex: 1 }}
            >
              {option.label}
            </Typography>
            {isActive && (
              <Iconify
                icon="solar:check-linear"
                width={16}
                sx={{ color: "primary.main" }}
              />
            )}
          </Box>
        );
      })}
    </Box>
  );
});

ThemeSelectorChild.displayName = "ThemeSelectorChild";

ThemeSelectorChild.propTypes = {
  setOpen: PropTypes.func,
};

const ThemeSelector = React.forwardRef(
  ({ open, anchorEl, setOpen, autoFocus }, popperTimeoutRef) => (
    <SwitcherSubmenu open={open} anchorEl={anchorEl} setOpen={setOpen} autoFocus={autoFocus} label="Appearance">
      {(changeOpen) => <ThemeSelectorChild setOpen={changeOpen} ref={popperTimeoutRef} />}
    </SwitcherSubmenu>
  ),
);

ThemeSelector.displayName = "ThemeSelector";

ThemeSelector.propTypes = {
  autoFocus: PropTypes.bool,
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  setOpen: PropTypes.func,
};

export default ThemeSelector;
