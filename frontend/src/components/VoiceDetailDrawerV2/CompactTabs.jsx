import React from "react";
import PropTypes from "prop-types";
import { Tab, Tabs } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Compact underline-style tab bar. Drawer panes are narrow (about 280px
 * at a 1024px window), so the tabs wrap onto extra rows instead of
 * hiding behind chevrons: every tab label stays readable.
 * The strip's variant and scroll buttons are set here, not left to the
 * theme's MuiTabs defaults, so the app and the tests render the same
 * strip. It stays scrollable, so a pane dragged narrower than one tab
 * still scrolls sideways, and any chevrons MUI shows keep their compact
 * size.
 * MUI's sliding indicator assumes a single row, so it is hidden and the
 * selected tab draws its own underline with the same token the theme
 * indicator uses (border.selected).
 */
const CompactTabs = ({ value, onChange, tabs }) => {
  return (
    <Tabs
      value={value}
      onChange={onChange}
      variant="scrollable"
      scrollButtons="auto"
      allowScrollButtonsMobile
      sx={(theme) => ({
        boxShadow: "none",
        borderBottom: "1px solid",
        borderColor: "divider",
        "& .MuiTabs-flexContainer": { gap: 0, flexWrap: "wrap" },
        "& .MuiTabs-indicator": { display: "none" },
        "& .MuiTab-root": {
          minWidth: "unset !important",
          marginRight: "0 !important",
          gap: "4px",
          whiteSpace: "nowrap",
          fontFamily: "'Inter', sans-serif",
          letterSpacing: 0,
        },
        "& .MuiTab-root.Mui-selected": {
          boxShadow: `inset 0 -2px 0 ${
            theme.palette.border?.selected || theme.palette.secondary.main
          }`,
        },
        "& .MuiTabs-scrollButtons": {
          width: 24,
          "&.Mui-disabled": { opacity: 0.3 },
        },
      })}
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
