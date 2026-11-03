import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";

export default function WidgetJsonTree({ config }) {
  const data = config.data ?? {};

  return (
    <Box sx={{ height: "100%", overflow: "auto", p: 1 }}>
      <CustomJsonViewer object={data} />
    </Box>
  );
}

WidgetJsonTree.propTypes = { config: PropTypes.object.isRequired };
