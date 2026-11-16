import { Box, Tab } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CustomTooltip from "src/components/tooltip";
import { SegmentedTabs } from "src/components/tabs/tabs";

// eslint-disable-next-line react-refresh/only-export-components
export const CONTAINER_SMALL =
  "@container (min-width: 350px) and (max-width: 750px)";
// eslint-disable-next-line react-refresh/only-export-components
export const CONTAINER_XS = "@container (max-width: 349px)";

const CustomAgentTabs = ({
  value,
  onChange,
  tabs,
  containerResponsive = false,
}) => {
  const outerSx = containerResponsive
    ? {
        maxWidth: "100%",
        [CONTAINER_SMALL]: { width: "100%" },
      }
    : {};

  return (
    <Box sx={outerSx}>
      <SegmentedTabs
        value={value}
        onChange={onChange}
        sx={{
          alignSelf: "flex-start",
          ...(containerResponsive && {
            [CONTAINER_SMALL]: {
              width: "100%",
              "& .MuiTabs-flexContainer": { width: "100%" },
              "& .MuiTab-root": { flex: 1 },
            },
          }),
        }}
      >
        {tabs.map((tab) => {
          const tabElement = (
            <Tab
              key={tab.value}
              label={tab.label}
              value={tab.value}
              disabled={tab.disabled}
            />
          );

          return tab.disabled ? (
            <CustomTooltip
              key={tab.value}
              title="Not available yet"
              show
              size="small"
              arrow
              placement="top"
            >
              <Box>{tabElement}</Box>
            </CustomTooltip>
          ) : (
            tabElement
          );
        })}
      </SegmentedTabs>
    </Box>
  );
};

CustomAgentTabs.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  tabs: PropTypes.array,
  containerResponsive: PropTypes.bool,
};

export default CustomAgentTabs;
