// VideoLens (`kind="video"`, thin this round): each run's clip
// side by side through MediaValue `size="card"` (`<video controls
// preload="metadata">`). A shared play / seek and the poster strip are not
// built this round; the lab says so.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import MediaValue from "src/sections/common/Media/MediaValue";
import LaneLabel from "./LaneLabel";

export default function VideoLens({ lanes = [], height }) {
  return (
    <Stack
      data-modality-lens="video"
      tabIndex={0}
      aria-label="Video lens: each run's clip side by side"
      sx={{
        height: height ?? "100%",
        minHeight: 0,
        p: 1.5,
        gap: 1,
        overflow: "auto",
        outline: "none",
      }}
    >
      <Typography typography="s3" color="text.secondary">
        Side by side · each clip plays on its own (a shared play and seek are
        not built yet)
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: `repeat(${Math.max(1, lanes.length)}, minmax(0, 1fr))`,
          gap: 1.5,
        }}
      >
        {lanes.map((l) => (
          <Stack
            key={l.runId}
            gap={0.5}
            sx={{ minWidth: 0 }}
            data-video-lane={l.runId}
          >
            <LaneLabel lane={l} dense />
            <MediaValue value={l.value} size="card" maxHeight={360} />
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}

VideoLens.propTypes = {
  lanes: PropTypes.array,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};
