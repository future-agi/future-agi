import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";

export default function KeysEmptyState({ onCreate }) {
  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 2,
        p: 2,
        textAlign: "center",
      }}
    >
      <Typography typography="s1" color="text.secondary">
        No API keys yet. Create a personal key to use the SDK, CLI or OTLP.
      </Typography>
      <Button variant="contained" onClick={onCreate}>
        Create a key
      </Button>
    </Box>
  );
}
KeysEmptyState.propTypes = { onCreate: PropTypes.func.isRequired };
