import PropTypes from "prop-types";
import { Alert } from "@mui/material";

const mustPassFailedText = (names) => `A must-pass case failed: ${names.join(", ")}`;

// A must-pass case that failed blocks the release, so the run says so above its results.
export default function MustPassBanner({ names }) {
  if (!names?.length) return null;
  return (
    <Alert severity="error" data-testid="must-pass-banner">
      {mustPassFailedText(names)}
    </Alert>
  );
}

MustPassBanner.propTypes = { names: PropTypes.arrayOf(PropTypes.string) };
