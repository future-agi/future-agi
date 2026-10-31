import { Box, Popper, Typography } from "@mui/material";
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
      onMouseLeave={() => {
        popperTimeoutRef.current = setTimeout(() => {
          setOpen(false);
        }, 100);
      }}
      sx={{
        backgroundColor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
        boxShadow: "0px 4px 20px rgba(0, 0, 0, 0.1)",
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
              "&:hover": {
                backgroundColor: "background.neutral",
              },
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
  ({ open, anchorEl, setOpen }, popperTimeoutRef) => {
    return (
      <Popper
        open={open}
        anchorEl={anchorEl}
        placement="right-start"
        modifiers={[
          {
            name: "offset",
            options: {
              offset: [0, 8],
            },
          },
        ]}
        style={{ zIndex: 1300 }}
      >
        <ThemeSelectorChild setOpen={setOpen} ref={popperTimeoutRef} />
      </Popper>
    );
  },
);

ThemeSelector.displayName = "ThemeSelector";

ThemeSelector.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  setOpen: PropTypes.func,
};

export default ThemeSelector;
