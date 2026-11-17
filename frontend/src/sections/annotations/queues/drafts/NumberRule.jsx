import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

/**
 * The line under a numeric score (KH-SCOPE §6 B1, mount M-B3): the label's
 * rule (`0 – 1 · steps of 0.1`), or, while a typed number is refused, the
 * reason (`Helpfulness must be between 0 and 1 (you entered 1.4).`).
 * `entry` = useNumberDraft's result.
 */
export default function NumberRule({ entry }) {
  const text = entry?.error ?? entry?.hint;
  if (!text) return null;
  return (
    <Typography
      typography="s3"
      color={entry.error ? "error.main" : "text.secondary"}
      role={entry.error ? "alert" : undefined}
      data-testid="numeric-rule"
      sx={{ display: "block" }}
    >
      {text}
    </Typography>
  );
}

NumberRule.propTypes = {
  entry: PropTypes.shape({
    error: PropTypes.string,
    hint: PropTypes.string,
  }),
};
