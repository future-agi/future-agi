import React, { memo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

const GridIcon = memo(({ src, alt, sx, ...other }) => {
  return (
    <Box
      component="img"
      src={src}
      alt={alt}
      sx={{
        width: 20,
        height: 20,
        objectFit: "contain",
        verticalAlign: "middle",
        imageRendering: "crisp-edges",
        backfaceVisibility: "hidden",
        transform: "translateZ(0)",
        ...sx,
      }}
      {...other}
    />
  );
});

GridIcon.displayName = "GridIcon";

GridIcon.propTypes = {
  src: PropTypes.string.isRequired,
  alt: PropTypes.string,
  sx: PropTypes.object,
};

export default GridIcon;
