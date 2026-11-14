// Tooltip component for input/output columns - optimized and lightweight

import React, { memo } from "react";
import CustomJsonViewer from "../../../../components/custom-json-viewer/CustomJsonViewer";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";

// AG Grid passes { value, colDef } as props
const IPOPTooltipComponent = memo(({ value }) => {
  const theme = useTheme();
  const tooltipStyle = {
    maxHeight: 200,
    minWidth: "200px",
    overflowY: "auto",
    backgroundColor: "var(--bg-paper)",
    padding: "16px",
    fontSize: "12px",
    lineHeight: "22px",
    color: "var(--text-primary)",
    fontFamily: "Roboto, sans-serif",
    fontWeight: "400",
    fontStyle: "normal",
    maxWidth: "400px",
    boxShadow: theme.customShadows?.dropdown,
    borderRadius: "8px",
    border: "1px solid var(--border-default)",
  };

  if (typeof value === "object" && value !== null) {
    return (
      <div style={tooltipStyle}>
        <CustomJsonViewer object={value} />
      </div>
    );
  }
  return <div style={tooltipStyle}>{value}</div>;
});

IPOPTooltipComponent.displayName = "IPOPTooltipComponent";

IPOPTooltipComponent.propTypes = {
  value: PropTypes.any,
  colDef: PropTypes.object,
};

export default IPOPTooltipComponent;
