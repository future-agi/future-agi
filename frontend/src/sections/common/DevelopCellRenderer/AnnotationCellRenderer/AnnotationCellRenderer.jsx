import { Box, useTheme } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import RenderAnnotationInfo from "./RenderAnnotationInfo";
import { renderAnnotationValue } from "./renderAnnotationValue";

const AnnotationCellRenderer = ({ value, annotationData }) => {
  const theme = useTheme();
  const inner = renderAnnotationValue(value, theme);
  if (inner === null) return null;

  return (
    <Box
      sx={{
        padding: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "100%",
      }}
    >
      <Box
        sx={{
          lineHeight: "1.5",
          flex: 1,
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          alignItems: "center",
          overflow: "hidden",
        }}
      >
        {inner}
      </Box>
      <RenderAnnotationInfo annotationData={annotationData} />
    </Box>
  );
};

AnnotationCellRenderer.propTypes = {
  value: PropTypes.any,
  annotationData: PropTypes.object,
};

export default AnnotationCellRenderer;
