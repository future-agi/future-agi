import React from "react";
import PropTypes from "prop-types";
import { Tab, Tabs } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Compact underline-style tab bar. Uses MUI `Tabs` with
 * `variant="scrollable"` + `scrollButtons="auto"` so overflowing tabs
 * get chevron arrows on the sides (same as the trace drawer's
 * SpanDetailPane tab row). Styling matches that file so both drawers
 * feel like one product.
 */
const CompactTabs = ({ value, onChange, tabs }) => {
  return (
    <Tabs
      value={value}
      onChange={onChange}
      variant="scrollable"
      scrollButtons="auto"
      allowScrollButtonsMobile
      sx={{
        borderBottom: "1px solid",
        borderColor: "divider",
        "& .MuiTabs-flexContainer": { gap: 0 },
        "& .MuiTab-root": {
          minWidth: "unset !important",
          marginRight: "0 !important",
          gap: "4px",
          fontFamily: "'Inter', sans-serif",
          letterSpacing: 0,
        },
        "& .MuiTabs-scrollButtons": {
          width: 24,
          "&.Mui-disabled": { opacity: 0.3 },
        },
      }}
    >
      {tabs?.map((tab) => (
        <Tab
          key={tab.value}
          value={tab.value}
          icon={tab.icon ? <Iconify icon={tab.icon} width={14} /> : undefined}
          iconPosition="start"
          label={tab.label}
        />
      ))}
    </Tabs>
  );
};

CompactTabs.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func,
  tabs: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
        .isRequired,
      label: PropTypes.string.isRequired,
      icon: PropTypes.string,
    }),
  ),
};

export default CompactTabs;
