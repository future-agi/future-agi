import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const DatasetCompareAnnotationSummary = () => {
  return (
    <Box
      sx={{
        marginTop: "16px",
        borderRadius: "4px",
        backgroundColor: "status.info.bg",
        border: "1px solid",
        borderColor: "status.info.border",
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
      }}
    >
      <Typography
        typography={"s1"}
        fontWeight={"fontWeightSemiBold"}
        color="status.info.fg"
      >
        No Comparison Available
      </Typography>
      <Typography
        typography={"s3"}
        fontWeight={"fontWeightRegular"}
        color="status.info.fg"
      >
        Select an individual dataset to view its annotation summary
      </Typography>
    </Box>
  );
};

export default DatasetCompareAnnotationSummary;

DatasetCompareAnnotationSummary.propTypes = {
  selectedColumns: PropTypes.array,
  isCompare: PropTypes.bool,
  currentDataset: PropTypes.string,
  selectedDatasets: PropTypes.array,
  selectedIndex: PropTypes.number,
  commonColumn: PropTypes.array,
  baseColumn: PropTypes.string,
  selectedDatasetData: PropTypes.array,
};
