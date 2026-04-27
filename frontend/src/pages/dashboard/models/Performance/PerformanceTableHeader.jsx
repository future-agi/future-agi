import {
  Box,
  Divider,
  MenuItem,
  Popover,
  Tab,
  Tabs,
  useTheme,
} from "@mui/material";
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";

const options = [
  { label: "Latest", value: "latest" },
  { label: "Earliest", value: "earliest" },
  { label: "Low to high score", value: "lowestScore" },
  { label: "High to low score", value: "highestScore" },
];

const secondaryTabs = [
  { label: "Data", value: "data" },
  { label: "Tag Distribution", value: "tagDistribution" },
  { label: "Graph Datapoints", value: "graphDatapoints" },
];

const PerformanceDetailSection = ({
  orderOption,
  setOrderOption,
  selectedDataset,
  setSelectedDataset,
  datasets,
  selectedDetailTab,
  setSelectedDetailTab,
  children,
}) => {
  const [isSortOpen, setSortOpen] = useState(false);

  const sortRef = useRef();

  const theme = useTheme();

  return (
    <Box>
      <Box
        sx={{
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Tabs
          value={selectedDataset}
          onChange={(e, value) => setSelectedDataset(value)}
          textColor="primary"
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
            },
          }}
        >
          {datasets.map((dataset, idx) => (
            <Tab label={`Dataset ${idx + 1}`} value={idx} key={idx} />
          ))}
        </Tabs>
      </Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          borderBottom: 1,
          borderColor: "divider",
          alignItems: "center",
        }}
      >
        <Tabs
          value={selectedDetailTab}
          onChange={(e, value) => setSelectedDetailTab(value)}
          textColor="primary"
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
            },
          }}
        >
          {secondaryTabs.map(({ label, value }) => (
            <Tab label={label} value={value} key={value} />
          ))}
        </Tabs>
        <Divider />
        <Box sx={{ display: "flex", gap: 1 }}>
          {/* <Button
            ref={sortRef}
            startIcon={
              <Iconify icon="bi:filter-left" sx={{ color: "primary.main" }} />
            }
            size="small"
            onClick={() => setSortOpen(true)}
          >
            Sort
          </Button> */}
          {/* <Button
            variant="soft"
            size="small"
            onClick={() => {
              trackEvent(Events.metricPerformanceExportClick);
              generateExport();
            }}
          >
            Export
          </Button> */}
        </Box>
      </Box>
      <Box sx={{ paddingY: 1 }}>{children}</Box>

      <Popover
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        anchorEl={sortRef?.current}
        open={isSortOpen}
        onClose={() => setSortOpen(false)}
      >
        {options.map(({ label, value }) => (
          <MenuItem
            selected={value === orderOption}
            onClick={() => {
              // trackEvent(Events.metricPerformanceSortChange, {
              //   "Old Sort": orderOption,
              //   "New Sort": value,
              // });
              setOrderOption(value);
              setSortOpen(false);
            }}
            key={label}
          >
            {label}
          </MenuItem>
        ))}
      </Popover>
    </Box>
  );
};

PerformanceDetailSection.propTypes = {
  generateExport: PropTypes.func,
  orderOption: PropTypes.string,
  setOrderOption: PropTypes.func,
  selectedDataset: PropTypes.number,
  setSelectedDataset: PropTypes.func,
  datasets: PropTypes.array,
  selectedDetailTab: PropTypes.string,
  setSelectedDetailTab: PropTypes.func,
  children: PropTypes.node,
};

export default PerformanceDetailSection;
