// A dialog's own status line (W4). The app hides any toast that would cross
// an open dialog or drawer (components/snackbar/toastPlacement.js: "the
// drawer's own status line says it"), so a change made inside Dataset
// settings or the Share dialog also states its result here. The toast still
// shows whenever there is room for it. The state lives in useStatusLine.js.
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
