import React from "react";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import { allExpanded, defaultStyles } from "react-json-view-lite";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";

export default function UploadedJSON({
  uploadedJson,
  uploadedFileName,
  setUploadedJson,
}) {
  return (
    <Box
      sx={{
        height: "auto",
        overflow: "auto",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: (theme) => theme.spacing(0.5, 0.5, 0, 0),
      }}
    >
      <Stack
        sx={{
          padding: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          position: "sticky",
          top: 0,
          backgroundColor: "background.paper",
          zIndex: 1,
        }}
        direction="row"
        justifyContent="space-between"
        alignItems="center"
      >
        <Typography
          typography="s2_1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          {uploadedFileName || "Uploaded JSON"}
        </Typography>
        <IconButton
          sx={{
            color: "status.danger.fg",
          }}
          onClick={() => setUploadedJson(null)}
        >
          <SvgColor
            src="/assets/icons/ic_delete.svg"
            sx={{ width: 20, height: 20 }}
          />
        </IconButton>
      </Stack>
      <Box
        sx={{
          padding: 1,
        }}
      >
        <CustomJsonViewer
          object={uploadedJson}
          shouldExpandNode={allExpanded}
          clickToExpandNode={true}
          style={defaultStyles}
        />
      </Box>
    </Box>
  );
}

UploadedJSON.propTypes = {
  uploadedJson: PropTypes.object.isRequired,
  uploadedFileName: PropTypes.string,
  setUploadedJson: PropTypes.func.isRequired,
};
