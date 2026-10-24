import { IconButton, Stack, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify/iconify";

const PlaygroundHeader = ({ evaluationName, onClose }) => {
  return (
    <Stack gap={0.5}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography typography="m2" fontWeight="fontWeightSemiBold">
          {evaluationName} - Playground
        </Typography>
        <IconButton onClick={onClose} size="small">
          <Iconify icon="solar:close-linear" color="text.primary" />
        </IconButton>
      </Stack>
      <Typography typography="s1">
        Configure variables to test evaluation
      </Typography>
    </Stack>
  );
};

PlaygroundHeader.propTypes = {
  evaluationName: PropTypes.string,
  onClose: PropTypes.func,
};

export default PlaygroundHeader;
