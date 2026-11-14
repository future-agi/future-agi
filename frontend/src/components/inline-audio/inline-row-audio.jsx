import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

// ── Inline audio player ─────────────────────────────────────────
export const InlineAudio = ({ src }) => (
  <Box
    component="audio"
    controls
    preload="metadata"
    src={src}
    sx={{
      width: "100%",
      maxWidth: 420,
      height: 32,
      "&::-webkit-media-controls-panel": {
        backgroundColor: (theme) =>
          theme.palette.mode === "dark" ? "rgba(255,255,255,0.04)" : undefined,
      },
    }}
  >
    Your browser does not support audio playback.
  </Box>
);

InlineAudio.propTypes = { src: PropTypes.string.isRequired };

// ── Group of labeled audio players (for nested recording objects) ──
export const RecordingGroup = ({ tracks }) => (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75, py: 0.25 }}>
    {tracks.map(({ label, url }) => (
      <Box
        key={label + url}
        sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}
      >
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "12px", fontFamily: "monospace" }}
        >
          {label}
        </Typography>
        <InlineAudio src={url} />
      </Box>
    ))}
  </Box>
);

RecordingGroup.propTypes = {
  tracks: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      url: PropTypes.string.isRequired,
    }),
  ).isRequired,
};
