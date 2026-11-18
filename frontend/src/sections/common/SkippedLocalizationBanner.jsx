import PropTypes from "prop-types";
import { Alert } from "@mui/material";

const DEFAULT_SKIPPED_LOCALIZATION_MESSAGE =
  "Error localization was skipped — input data isn't available to localize on.";

export default function SkippedLocalizationBanner({ message, sx }) {
  return (
    <Alert severity="info" sx={sx}>
      {message || DEFAULT_SKIPPED_LOCALIZATION_MESSAGE}
    </Alert>
  );
}

SkippedLocalizationBanner.propTypes = {
  message: PropTypes.string,
  sx: PropTypes.object,
};
