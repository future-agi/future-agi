// A video in a cell (thin): poster 44 × 26 + duration; click opens the
// lightbox with `<video controls preload="metadata">`.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { NUM_SX } from "src/sections/common/format/fmt";
import MediaLightbox from "./MediaLightbox";
import { clockOf } from "./mediaModel";

export default function VideoPoster({ mediaRef }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Stack
        component="button"
        type="button"
        direction="row"
        alignItems="center"
        gap={0.75}
        aria-label={`Play video ${mediaRef.name ?? ""}`.trim()}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        data-video-poster=""
        sx={{
          p: 0,
          border: 0,
          bgcolor: "transparent",
          cursor: "pointer",
          color: "text.secondary",
          minWidth: 0,
        }}
      >
        <Box
          sx={{
            position: "relative",
            width: 44,
            height: 26,
            flexShrink: 0,
            borderRadius: 0.5,
            overflow: "hidden",
            bgcolor: "grey.800",
          }}
        >
          {mediaRef.poster_url ? (
            <img
              src={mediaRef.poster_url}
              alt={mediaRef.alt ?? "Video poster"}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
          ) : null}
          <Iconify
            icon="mdi:play"
            width={14}
            sx={{
              position: "absolute",
              inset: 0,
              m: "auto",
              color: "common.white",
            }}
          />
        </Box>
        <Typography typography="s3" sx={NUM_SX} noWrap>
          {Number.isFinite(mediaRef.duration_ms)
            ? clockOf(mediaRef.duration_ms)
            : mediaRef.name ?? "Video"}
        </Typography>
      </Stack>
      <MediaLightbox
        open={open}
        items={[mediaRef]}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

VideoPoster.propTypes = { mediaRef: PropTypes.object.isRequired };
