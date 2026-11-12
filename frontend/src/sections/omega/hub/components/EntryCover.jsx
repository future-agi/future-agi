import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { alpha } from "@mui/material/styles";

import { coverColors, coverEmoji, monogram } from "../lib/cover";

// The entry's cover tile (the publisher's own `cover` in the manifest): its
// emoji on one flat tint of the cover colour, with a hairline border in the
// same hue. No blend of two colours, no glow and no shadow, so the tile reads
// as a small mark beside the title and never as decoration.
export function EntryCover({ cover, size = 44 }) {
  const [, tint] = coverColors(cover);
  return (
    <Box
      aria-hidden="true"
      data-testid="hub-entry-cover"
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size * 0.5,
        lineHeight: 1,
        bgcolor: alpha(tint, 0.16),
        border: "1px solid",
        borderColor: alpha(tint, 0.28),
      }}
    >
      {coverEmoji(cover)}
    </Box>
  );
}

EntryCover.propTypes = {
  cover: PropTypes.object,
  size: PropTypes.number,
};

// A publisher's avatar: their uploaded image (served by the registry), or a
// generated monogram. Never a third-party image.
export function PublisherAvatar({ publisher, size = 32 }) {
  const { letter, color } = monogram(publisher?.displayName, publisher?.handle);
  if (publisher?.avatarUrl) {
    return (
      <Box
        component="img"
        src={publisher.avatarUrl}
        alt=""
        sx={{
          width: size,
          height: size,
          borderRadius: "50%",
          objectFit: "cover",
          flexShrink: 0,
        }}
      />
    );
  }
  return (
    <Box
      aria-hidden="true"
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: alpha(color, 0.16),
        color,
        fontWeight: 600,
        fontSize: size * 0.44,
      }}
    >
      {letter}
    </Box>
  );
}

PublisherAvatar.propTypes = {
  publisher: PropTypes.object,
  size: PropTypes.number,
};

export default EntryCover;
