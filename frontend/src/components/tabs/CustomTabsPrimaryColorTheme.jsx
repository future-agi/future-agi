import { Box, Tab } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { SegmentedTabs } from "./tabs";

const CustomTabsPrimaryColorTheme = ({ value, onChange, tabs }) => {
  return (
    <Box sx={{ alignSelf: "flex-start", mb: 1 }}>
      <SegmentedTabs value={value} onChange={onChange}>
        {tabs.map((tab) => (
          <Tab
            key={tab.value}
            label={tab.label}
            value={tab.value}
            disabled={tab.disabled}
          />
        ))}
      </SegmentedTabs>
    </Box>
  );
};

CustomTabsPrimaryColorTheme.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  tabs: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
      disabled: PropTypes.bool,
    }),
  ).isRequired,
};

export default CustomTabsPrimaryColorTheme;
