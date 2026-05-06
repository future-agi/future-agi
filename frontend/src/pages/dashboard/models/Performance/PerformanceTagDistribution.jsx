import React from "react";
import PropTypes from "prop-types";
import {
  FormControl,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
} from "@mui/material";
import TopTagsGraph from "./TopTagsGraph";
import AllTagsGraph from "./AllTagsGraph";

const tagDistributionTypes = [
  { label: "All Tags", value: "all" },
  { label: "Good Tags", value: "good" },
  { label: "Bad Tags", value: "bad" },
];

const PerformanceTagDistribution = ({
  tagDistribution,
  selectedTagDistributionType,
  setSelectedTagDistributionType,
  isLoading,
}) => {
  return (
    <>
      {isLoading && <LinearProgress />}
      <Paper elevation={2} sx={{ padding: 2 }}>
        <FormControl size="small" sx={{ minWidth: "216px" }}>
          <Select
            size="small"
            value={selectedTagDistributionType}
            onChange={(e) => setSelectedTagDistributionType(e.target.value)}
          >
            {tagDistributionTypes.map(({ label, value }) => (
              <MenuItem key={value} value={value}>
                {label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        {selectedTagDistributionType === "all" && !isLoading && (
          <AllTagsGraph data={tagDistribution} />
        )}
        {selectedTagDistributionType === "good" && !isLoading && (
          <TopTagsGraph data={tagDistribution} type="good" />
        )}
        {selectedTagDistributionType === "bad" && !isLoading && (
          <TopTagsGraph data={tagDistribution} type="bad" />
        )}
      </Paper>
    </>
  );
};

PerformanceTagDistribution.propTypes = {
  tagDistribution: PropTypes.object,
  selectedTagDistributionType: PropTypes.string,
  setSelectedTagDistributionType: PropTypes.func,
  isLoading: PropTypes.bool,
};
export default PerformanceTagDistribution;
