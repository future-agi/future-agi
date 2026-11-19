import { Box, IconButton, TextField, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import logger from "src/utils/logger";

const ConfigureEdgeForm = ({ onClose, edge, onChange }) => {
  logger.debug({ edge });
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: "100%",
      }}
    >
      <Box
        sx={{
          padding: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Condition
        </Typography>
        <IconButton
          onClick={onClose}
          sx={{
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
      <Box sx={{ padding: 2 }}>
        <TextField
          label="Condition"
          value={edge?.data?.prompt}
          onChange={(e) => onChange(edge.id, { prompt: e.target.value })}
          size="small"
          multiline
          rows={4}
          fullWidth
          placeholder="Condition for switching node"
        />
      </Box>
    </Box>
  );
};

ConfigureEdgeForm.propTypes = {
  onClose: PropTypes.func,
  edge: PropTypes.object,
  onChange: PropTypes.func,
};

export default ConfigureEdgeForm;
