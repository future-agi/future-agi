// Tooltip component for input/output columns - optimized and lightweight

import React, { memo } from "react";
import CustomJsonViewer from "../../../../components/custom-json-viewer/CustomJsonViewer";
import PropTypes from "prop-types";

// AG Grid passes { value, colDef } as props
const IPOPTooltipComponent = memo(({ value }) => {
  const tooltipStyle = {
    maxHeight: 200,
    minWidth: "200px",
    overflowY: "auto",
    backgroundColor: "var(--bg-paper)",
    padding: "16px",
    fontSize: "13px",
    lineHeight: "22px",
    color: "var(--text-primary)",
    fontFamily: "Roboto, sans-serif",
    fontWeight: "400",
    fontStyle: "normal",
    maxWidth: "400px",
    // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
    boxShadow: "0px 4px 12px 0px rgba(0, 0, 0, 0.1)",
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
