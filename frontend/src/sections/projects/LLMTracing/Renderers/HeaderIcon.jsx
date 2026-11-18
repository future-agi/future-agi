import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { HEADER_ICON_CONFIG } from "./common";

const HeaderIcon = ({ displayName, isGroup, isEvaluationMetric }) => {
  const iconConfig = HEADER_ICON_CONFIG[displayName];
  const iconSize = { width: 16, height: 16 };

  const wrapperStyle = {
    display: "flex",
    alignItems: "center",
  };

  // Group or Evaluation Metrics icon
  if (isGroup || isEvaluationMetric) {
    return (
      <div style={wrapperStyle}>
        <SvgColor src="/assets/icons/ic_completed.svg" sx={iconSize} />
      </div>
    );
  }

  // Specific column icon
  if (iconConfig) {
    return (
      <div style={wrapperStyle}>
        {iconConfig.type === "iconify" ? (
          <Iconify
            icon={iconConfig.icon}
            color={iconConfig.color}
            width="16px"
            height="16px"
          />
        ) : (
          <SvgColor src={iconConfig.src} sx={iconSize} />
        )}
      </div>
    );
  }

  // Default icon
  return (
    <div style={wrapperStyle}>
      <SvgColor
        src="/assets/icons/ic_col_header.svg"
        sx={{ ...iconSize, bgcolor: "text.primary" }}
      />
    </div>
  );
};

HeaderIcon.propTypes = {
  displayName: PropTypes.string,
  isGroup: PropTypes.bool,
  isEvaluationMetric: PropTypes.bool,
};

export default HeaderIcon;
