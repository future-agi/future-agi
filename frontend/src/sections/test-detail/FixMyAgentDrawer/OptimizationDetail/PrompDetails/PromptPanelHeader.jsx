import { Box, IconButton, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { copyToClipboard } from "../../../../../utils/utils";
import { enqueueSnackbar } from "src/components/snackbar";
import SvgColor from "../../../../../components/svg-color";

const PromptPanelHeader = ({ title, prompt }) => {
  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <Typography typography="s2" fontWeight="fontWeightMedium">
        {title}
      </Typography>
      <IconButton
        size="small"
        onClick={() => {
          copyToClipboard(prompt);
          enqueueSnackbar("Copied to clipboard", {
            variant: "success",
          });
        }}
        sx={{
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_copy.svg"
          sx={{ width: 12, height: 12 }}
          color="text.primary"
        />
      </IconButton>
    </Box>
  );
};

PromptPanelHeader.propTypes = {
  title: PropTypes.string,
  prompt: PropTypes.string,
};

export default PromptPanelHeader;
