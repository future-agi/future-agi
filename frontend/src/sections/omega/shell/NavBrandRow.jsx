import React from "react";
import PropTypes from "prop-types";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";

import SvgColor from "src/components/svg-color";
import { useSettingsContext } from "src/components/settings";

import ProductSwitcher from "./ProductSwitcher";

// The vertical nav's top row when Omega is enabled: the Future AGI mark is
// the product switcher, and the collapse toggle stays where Logo puts it.
// Inside the narrow-screen drawer there is nothing to collapse, so the toggle
// is left out there.
export default function NavBrandRow({ showCollapse = true }) {
  const settings = useSettingsContext();

  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{ mb: "4px" }}
      data-testid="omega-nav-brand-row"
    >
      <ProductSwitcher />
      {showCollapse && (
        <IconButton
          aria-label="Collapse sidebar"
          onClick={() =>
            settings.onUpdate(
              "themeLayout",
              settings.themeLayout === "vertical" ? "mini" : "vertical",
            )
          }
          sx={{ color: "text.primary" }}
        >
          <SvgColor
            sx={{ height: "25px", width: "25px" }}
            src="/assets/icons/navbar/ic_nav_toggle.svg"
          />
        </IconButton>
      )}
    </Stack>
  );
}

NavBrandRow.propTypes = {
  showCollapse: PropTypes.bool,
};
