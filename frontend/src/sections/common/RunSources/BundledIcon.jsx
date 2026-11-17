// A generic concept icon (Material Design Icons) from the bundle, so the
// run-source cards and run-kind badges never wait on the Iconify API. Same
// props as Iconify; an id with no bundled copy falls back to Iconify.
import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import Iconify from "src/components/iconify";
import { GENERIC_ICONS } from "./brandLogos";

export default function BundledIcon({ icon, width = 20, sx, ...other }) {
  const svg = typeof icon === "string" ? GENERIC_ICONS[icon] : null;
  if (!svg) return <Iconify icon={icon} width={width} sx={sx} {...other} />;
  return (
    <Box
      component="span"
      aria-hidden
      data-bundled-icon={icon}
      dangerouslySetInnerHTML={{ __html: svg }}
      sx={{
        width,
        height: width,
        flexShrink: 0,
        display: "inline-flex",
        "& svg": { width: "100%", height: "100%", display: "block" },
        ...sx,
      }}
      {...other}
    />
  );
}

BundledIcon.propTypes = {
  icon: PropTypes.oneOfType([PropTypes.element, PropTypes.string]),
  width: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  sx: PropTypes.object,
};
