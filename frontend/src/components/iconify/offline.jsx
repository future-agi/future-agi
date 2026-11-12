import React, { forwardRef, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Icon as OfflineIcon } from "@iconify/react/offline";

import { hasRegisteredIcon } from "./registry";

// This entry point contains no API module, providers, cache or network loader.
// The Vite alias also covers callers that import Icon without the MUI wrapper.
export const ICONIFY_API_ENABLED = false;

const missingIcon = {
  width: 24,
  height: 24,
  body: '<rect x="2" y="2" width="20" height="20" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 8a3 3 0 1 1 4 2.83c-.8.3-1 1-1 2.17m0 3v1" fill="none" stroke="currentColor" stroke-width="2"/>',
};

export const Icon = forwardRef(({ icon, className, onLoad, ...props }, ref) => {
  // Iconify accepts the legacy "gg-check-o" spelling as well as "gg:check-o".
  const name =
    typeof icon === "string" && !icon.includes(":")
      ? icon.replace(/^([a-z0-9]+)-/, "$1:")
      : icon;
  const isName = typeof name === "string";
  const missing = isName && !hasRegisteredIcon(name);
  const loaded = useRef(null);
  useEffect(() => {
    if (!isName || missing) {
      loaded.current = null;
      return;
    }
    if (loaded.current !== icon) {
      loaded.current = icon;
      onLoad?.(icon);
    }
  }, [icon, isName, missing, onLoad]);
  return (
    <OfflineIcon
      {...props}
      ref={ref}
      className={
        [
          className,
          ...(isName
            ? [
                "iconify",
                missing
                  ? "iconify--fallback"
                  : `iconify--${name.split(":")[0]}`,
              ]
            : []),
        ]
          .filter(Boolean)
          .join(" ") || undefined
      }
      icon={missing ? missingIcon : name}
      {...(missing
        ? {
            "data-missing-icon": icon,
            ...(import.meta.env.DEV || import.meta.env.MODE === "test"
              ? {
                  "aria-label": `Missing offline icon: ${icon}`,
                  "aria-hidden": false,
                  role: "img",
                }
              : {}),
          }
        : {})}
    />
  );
});
Icon.displayName = "OfflineIcon";
Icon.propTypes = {
  icon: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  className: PropTypes.string,
  onLoad: PropTypes.func,
};
