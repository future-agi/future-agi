import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";

/**
 * Shown when a connector's detail record fails to load. Distinct from the
 * "no tools discovered" empty state, which blames the connector for a request
 * that never landed.
 */
export default function ConnectorDetailError({ message, onRetry }) {
  return (
    <Box sx={{ textAlign: "center", py: 3 }}>
      <Iconify
        icon="mdi:alert-circle-outline"
        width={28}
        sx={{ color: "error.main", mb: 1 }}
      />
      <Typography typography="s2_1" sx={{ color: "text.secondary", mb: 1.5 }}>
        {message}
      </Typography>
      <Button size="small" variant="outlined" onClick={onRetry}>
        Try again
      </Button>
    </Box>
  );
}

ConnectorDetailError.propTypes = {
  message: PropTypes.string,
  onRetry: PropTypes.func,
};
