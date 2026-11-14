import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const CustomHeader = (props) => {
  const { displayName, menuIcon } = props;
  const { dataType } = props; // Destructure dataType from props

  // Determine the icon based on the data type
  const icon =
    dataType === "text"
      ? "solar:notes-linear"
      : dataType === "integer"
        ? "solar:check-circle-linear"
        : "solar:question-circle-linear"; // Default icon if data type is unknown
  const iconColor =
    dataType === "text"
      ? "text.primary"
      : dataType === "integer"
        ? "status.info.fg"
        : "text.disabled";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      {/* Render Icon Based on Data Type */}
      <Iconify
        icon={icon}
        width={24}
        style={{ marginRight: "8px" }}
        sx={{ color: iconColor }}
      />
      {/* Display Header Name */}
      {displayName}
      {/* Optional Menu Icon */}
      {menuIcon && (
        <Iconify icon={menuIcon} width={20} style={{ marginLeft: "8px" }} />
      )}
    </div>
  );
};

// Validate Props
CustomHeader.propTypes = {
  displayName: PropTypes.string.isRequired,
  menuIcon: PropTypes.string,
  dataType: PropTypes.string, // Accept dataType as a prop
};

export default CustomHeader;
