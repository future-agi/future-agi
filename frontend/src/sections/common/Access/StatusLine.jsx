// A dialog's own status line. A change made inside Dataset settings or the
// Share dialog states its result here as well as in a toast, so the result
// stays next to the controls that made it. The toast shows beside an open
// dialog or drawer, or over it when there is no room beside it
// (components/snackbar/toastPlacement.js). The state lives in useStatusLine.js.
import { Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

export default function StatusLine({ status, testId }) {
  if (!status?.text) return null;
  const error = status.tone === "error";
  return (
    <Stack
      direction="row"
      spacing={0.5}
      alignItems="center"
      role={error ? "alert" : "status"}
      aria-live="polite"
      data-testid={testId}
      data-tone={status.tone}
    >
      <Iconify
        icon={error ? "solar:danger-circle-bold" : "solar:check-circle-bold"}
        width={14}
        sx={{ color: error ? "error.main" : "success.main", flexShrink: 0 }}
      />
      <Typography variant="s3" color={error ? "error.main" : "text.secondary"}>
        {status.text}
      </Typography>
    </Stack>
  );
}

StatusLine.propTypes = {
  status: PropTypes.shape({
    text: PropTypes.string,
    tone: PropTypes.oneOf(["success", "error"]),
  }),
  testId: PropTypes.string,
};
