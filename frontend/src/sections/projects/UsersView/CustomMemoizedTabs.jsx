import React, { memo } from "react";
import { Tabs, Tab } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PropTypes from "prop-types";

/**
 * CustomTabs - A reusable, memoized tabs component.
 *
 * @param {Array<{ label: string, value: string }>} tabs - List of tab options.
 * @param {string} selectedTab - Currently selected tab value.
 * @param {Function} onChange - Callback when tab changes.
 * @param {object} [sx] - Optional sx styles for Tabs.
 */
const CustomTabs = memo(function CustomTabs({
  tabs,
  selectedTab,
  onChange,
  sx = {},
}) {
  const theme = useTheme();

  return (
    <Tabs
      value={selectedTab}
      onChange={onChange}
      aria-label="Custom Tabs"
      sx={{
        "& .MuiTab-root": {
          margin: "0 !important",
        },
        ...sx,
      }}
    >
      {tabs.map((tab) => (
        <Tab key={tab.value} label={tab.label} value={tab.value} />
      ))}
    </Tabs>
  );
});

CustomTabs.displayName = "CustomTabs";

CustomTabs.propTypes = {
  tabs: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ).isRequired,
  selectedTab: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  sx: PropTypes.object, // Optional sx styles for Tabs
};

export default CustomTabs;
