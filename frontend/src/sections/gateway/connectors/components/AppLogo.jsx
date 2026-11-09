import React from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";

import { LogoMark } from "../../components/frame";

// Only bundled, root-relative assets are loaded. Catalog vendor URLs fall
// back to initials immediately: no external request, even when offline.
function appLogoSrc(app, mode) {
  const icon = app?.icon;
  const src = icon && (mode === "dark" ? icon.dark : icon.light);
  return typeof src === "string" &&
    src.startsWith("/assets/") &&
    !src.includes("\\")
    ? src
    : null;
}

export default function AppLogo({ app, size = 32 }) {
  const theme = useTheme();
  return (
    <LogoMark
      name={app?.name}
      src={appLogoSrc(app, theme.palette.mode)}
      size={size}
      framed
    />
  );
}

AppLogo.propTypes = {
  app: PropTypes.shape({
    name: PropTypes.string,
    icon: PropTypes.shape({ light: PropTypes.string, dark: PropTypes.string }),
  }).isRequired,
  size: PropTypes.number,
};
