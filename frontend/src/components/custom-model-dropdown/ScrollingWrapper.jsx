import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useScrollEnd } from "src/hooks/use-scroll-end";

const ScrollingWrapper = ({
  position,
  fieldWidth,
  width,
  scrollFunction = () => {},
  dependancies = [],
  children,
}) => {
  const scrollRef = useScrollEnd(scrollFunction, [...dependancies]);

  return (
    <Box
      ref={scrollRef}
      sx={{
        maxHeight: position?.height,
        ...(fieldWidth && { minWidth: fieldWidth, maxWidth: "400px" }),
        ...(width && { width: width }),

        height: "100%",
        overflowY: "auto",
        position: "relative",
      }}
    >
      {children}
    </Box>
  );
};

export default ScrollingWrapper;

ScrollingWrapper.propTypes = {
  children: PropTypes.any,
  position: PropTypes.object,
  fieldWidth: PropTypes.string,
  width: PropTypes.number,
  scrollFunction: PropTypes.func,
  dependancies: PropTypes.array,
};
