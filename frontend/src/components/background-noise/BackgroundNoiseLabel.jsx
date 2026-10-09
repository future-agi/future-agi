import PropTypes from "prop-types";
import { Box } from "@mui/material";

import Iconify from "src/components/iconify";
import { QUIET_LINE } from "./backgroundNoise";

// One look for a call's background noise wherever it shows: the sound icon and
// the place, a muted icon for a quiet line, a dash when it isn't known.
export default function BackgroundNoiseLabel({ noise, iconWidth = 14 }) {
  if (!noise) {
    return (
      <Box component="span" sx={{ color: "text.disabled" }}>
        -
      </Box>
    );
  }
  const quiet = noise.key === QUIET_LINE;
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        whiteSpace: "nowrap",
        color: quiet ? "text.disabled" : "inherit",
      }}
    >
      <Iconify
        icon={quiet ? "solar:volume-cross-linear" : "solar:soundwave-linear"}
        width={iconWidth}
        sx={{
          flexShrink: 0,
          color: quiet ? "text.disabled" : "text.secondary",
        }}
        aria-hidden="true"
      />
      {noise.label}
    </Box>
  );
}

BackgroundNoiseLabel.propTypes = {
  noise: PropTypes.shape({
    key: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
  }),
  iconWidth: PropTypes.number,
};
