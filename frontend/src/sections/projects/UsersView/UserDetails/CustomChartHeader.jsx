import { Box, Typography, useTheme, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import PropTypes from "prop-types";
import React from "react";

const CustomChartHeader = ({ label, chartType, onToggleType, totalTraces }) => {
  const theme = useTheme();

  return (
    <Box px={2}>
      {/* Top Row: Label and Tab Toggle */}
      <Box display="flex" justifyContent="space-between" alignItems="center">
        <Typography
          variant="m2"
          fontWeight="fontWeightMedium"
          color={theme.palette.text.primary}
        >
          {label}
        </Typography>

        {/* Add container with proper spacing for shadow */}
        <Box>
          <Box sx={{ mb: 1.5 }}>
            <SegmentedTabs
              value={chartType}
              onChange={(e, value) => onToggleType(value)}
            >
              <Tab label="Line" value="line" />
              <Tab label="Bar" value="bar" />
            </SegmentedTabs>
          </Box>
        </Box>
      </Box>

      {/* Bottom Row: Total Traces */}
      <Box>
        <Typography variant="body2" color="text.disabled">
          <Box
            component="span"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {totalTraces}
          </Box>{" "}
          Total {label}
        </Typography>
      </Box>
    </Box>
  );
};

CustomChartHeader.propTypes = {
  label: PropTypes.string,
  chartType: PropTypes.string,
  onToggleType: PropTypes.func,
  totalTraces: PropTypes.any,
};

export default CustomChartHeader;
