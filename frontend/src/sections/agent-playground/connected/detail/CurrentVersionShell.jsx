import PropTypes from "prop-types";
import React from "react";
import { Box, CircularProgress, Typography } from "@mui/material";
import ClosableDialog from "src/components/custom-dialog/closable-dialog";

export default function CurrentVersionShell({ title, isError, onClose }) {
  return (
    <ClosableDialog
      open
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
    >
      <Box sx={{ p: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600, pr: 4 }}>
          {title}
        </Typography>
        {isError ? (
          <Typography
            sx={{ typography: "s2_1", color: "status.danger.fg", mt: 1 }}
          >
            Couldn&apos;t load this agent&apos;s current version. Close and try
            again.
          </Typography>
        ) : (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={24} />
          </Box>
        )}
      </Box>
    </ClosableDialog>
  );
}

CurrentVersionShell.propTypes = {
  title: PropTypes.string.isRequired,
  isError: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
