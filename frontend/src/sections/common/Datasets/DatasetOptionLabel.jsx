// One dataset in a picker (W9-SCOPE §5 A4, A6): a filled star when the user
// starred it, the folder path muted, then the name and an optional second
// part (rows). Renders only what the dataset carries: no star, no path.
import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { folderOfDataset } from "./pickerOrder";

export default function DatasetOptionLabel({
  dataset,
  suffix,
  showFolder = true,
}) {
  const folder = showFolder ? folderOfDataset(dataset) : "";
  return (
    <Box
      component="span"
      sx={{ display: "flex", alignItems: "center", gap: 0.75, minWidth: 0 }}
    >
      {dataset?.starred && (
        <Box
          component="span"
          data-testid="dataset-option-star"
          title="Starred"
          sx={{ display: "inline-flex", flexShrink: 0 }}
        >
          <Iconify icon="mdi:star" width={14} sx={{ color: "warning.main" }} />
        </Box>
      )}
      {folder && (
        <Typography
          component="span"
          typography="s2"
          color="text.disabled"
          noWrap
          sx={{ flexShrink: 1, minWidth: 0, maxWidth: "40%" }}
          data-testid="dataset-option-folder"
        >
          {`${folder} /`}
        </Typography>
      )}
      <Typography component="span" typography="s1" noWrap sx={{ minWidth: 0 }}>
        {dataset?.name ?? "Untitled dataset"}
        {suffix ? (
          <Typography component="span" typography="s1" color="text.secondary">
            {suffix}
          </Typography>
        ) : null}
      </Typography>
    </Box>
  );
}

DatasetOptionLabel.propTypes = {
  dataset: PropTypes.shape({
    name: PropTypes.string,
    starred: PropTypes.bool,
    folder: PropTypes.string,
  }),
  suffix: PropTypes.string,
  showFolder: PropTypes.bool,
};
