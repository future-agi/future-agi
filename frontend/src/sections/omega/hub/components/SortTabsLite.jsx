import React from "react";
import PropTypes from "prop-types";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";

import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./tabRow";

// A small tab row for one URL filter.
export function SortTabsLite({ value, onChange, options }) {
  return (
    <Tabs
      value={value}
      onChange={(_, next) => onChange(next)}
      {...TAB_ROW_PROPS}
      sx={{
        borderBottom: "1px solid",
        borderColor: "divider",
        minHeight: 36,
        "& .MuiTab-root": { minHeight: 36, typography: "s2" },
        ...TAB_ROW_SCROLL_SX,
      }}
    >
      {options.map(([v, label]) => (
        <Tab key={v || "all"} value={v} label={label} />
      ))}
    </Tabs>
  );
}

SortTabsLite.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array.isRequired,
};
