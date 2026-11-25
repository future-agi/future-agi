import React from "react";
import PropTypes from "prop-types";
import IconButton from "@mui/material/IconButton";
import { useResponsive } from "src/hooks/use-responsive";
import { useSettingsContext } from "src/components/settings";
import { NAV } from "../config-layout";
import { Box } from "@mui/material";

// ----------------------------------------------------------------------

export default function NavToggleButton({ sx, ...other }) {
  const settings = useSettingsContext();

  const lgUp = useResponsive("up", "xs");

  if (!lgUp) {
    return null;
  }

  return (
    <IconButton
      size="small"
      onClick={() =>
        settings.onUpdate(
          "themeLayout",
          settings.themeLayout === "vertical" ? "mini" : "vertical",
        )
      }
      sx={{
        width: 32, // or 36/40 depending on the size you want
        border: "1.2px solid var(--border-default)", // fallback border color
        bgcolor: "background.paper",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "absolute",
        left: NAV.W_VERTICAL - 50,
        zIndex: 2,
        padding: 0,
        "&:hover": {
          bgcolor: "background.paper",
        },
        ...sx,
      }}
      {...other}
    >
      {/* <Iconify
        width={12}
        icon={
          settings.themeLayout === "vertical"
            ? "solar:panel-left-open-linear"
            : "solar:panel-left-close-linear"
        }
        sx={{
          color: "text.disabled",
          "&:hover": {
            color: "text.secondary",
          },
         }}
      /> */}
      <Box
        component="img"
        src="/icons/newsidebar/Frame.svg"
        sx={{
          width: 20,
          height: 20,
          maxWidth: "unset",
        }}
      />
    </IconButton>
  );
}

NavToggleButton.propTypes = {
  sx: PropTypes.object,
};
