import React from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";

import { LogoMark } from "../../components/frame";
import { APP_LOGOS } from "../app-logos.generated";

// Only bundled, root-relative assets are loaded. Catalog vendor URLs fall
// back to initials immediately: no external request, even when offline.
function bundled(src) {
  return typeof src === "string" &&
    src.startsWith("/assets/") &&
    !src.includes("\\")
    ? src
    : null;
}

// The app's mark: the console's own logo for its catalog id (APP_LOGOS, its
// dark variant in dark mode when there is one), else the catalog's icon when
// that is a bundled asset, else none (LogoMark then shows the initials).
function appLogoSrc(app, mode) {
  const own =
    typeof app?.id === "string" && Object.hasOwn(APP_LOGOS, app.id)
      ? APP_LOGOS[app.id]
      : null;
  const ownSrc =
    own && bundled(mode === "dark" ? own.dark || own.light : own.light);
  if (ownSrc) return ownSrc;
  const icon = app?.icon;
  return bundled(icon && (mode === "dark" ? icon.dark : icon.light));
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
    id: PropTypes.string,
    name: PropTypes.string,
    icon: PropTypes.shape({ light: PropTypes.string, dark: PropTypes.string }),
  }).isRequired,
  size: PropTypes.number,
};
