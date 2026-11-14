import { Stack, Tab } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";

const tabOptions = [
  { label: "Markdown", value: "markdown", disabled: false },
  { label: "Raw", value: "raw", disabled: false },
];

export default function Filters({ viewType, setViewType }) {
  return (
    <Stack
      direction={"row"}
      alignItems={"center"}
      justifyContent={"space-between"}
    >
      <SegmentedTabs
        value={viewType}
        onChange={(e, value) => setViewType(value)}
      >
        {tabOptions.map((tab) => (
          <Tab
            key={tab.value}
            label={tab.label}
            value={tab.value}
            disabled={tab.disabled}
          />
        ))}
      </SegmentedTabs>
      {/* <FormSearchField
        size="small"
        placeholder="Search"
        disableUnderline
        searchQuery={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        sx={{
          width: "279px",
        }}
      /> */}
    </Stack>
  );
}

Filters.propTypes = {
  viewType: PropTypes.string,
  setViewType: PropTypes.func,
};
