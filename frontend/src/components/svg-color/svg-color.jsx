import { Box } from "@mui/material";
import { forwardRef, useEffect, useRef } from "react";
import { mergeRefs } from "src/utils/utils";
import PropTypes from "prop-types";

const SvgColor = forwardRef(({ src, width = 24, sx, ...other }, ref) => {
  const nodeRef = useRef(null);

  useEffect(() => {
    const node = nodeRef.current;
    if (!node || !src) return;

    const img = new Image();
    img.onload = () => {
      node.style.display = "none";
      void node.offsetHeight;
      node.style.display = "";
    };
    img.src = src;

    return () => {
      img.onload = null;
    };
  }, [src]);

  return (
    <Box
      component="span"
      className="svg-color"
      ref={mergeRefs(ref, nodeRef)}
      sx={{
        width,
        height: width,
        display: "inline-block",
        bgcolor: "currentColor",
        mask: `url(${src}) no-repeat center / contain`,
        WebkitMask: `url(${src}) no-repeat center / contain`,
        ...sx,
      }}
      {...other}
    />
  );
});
SvgColor.displayName = "SvgColor";
SvgColor.propTypes = {
  src: PropTypes.string,
  width: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  sx: PropTypes.object,
};

export default SvgColor;
