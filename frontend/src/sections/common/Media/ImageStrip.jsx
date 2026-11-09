// Images in a cell: up to `max` 28 px thumbs + `+n`; hover a thumb for a
// 240 px preview (dimensions in it); click or `Enter` opens MediaLightbox
// on that image. `Tab` moves between thumbs; the lightbox returns focus.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { NUM_SX } from "src/sections/common/format/fmt";
import MediaLightbox from "./MediaLightbox";
import { fittedSize } from "./mediaModel";

function Preview({ item }) {
  const box = fittedSize(item.width || 240, item.height || 180, 240, 240);
  return (
    <Stack gap={0.5} sx={{ p: 0.5 }}>
      <img
        src={item.url}
        alt={item.alt ?? item.name ?? "Image preview"}
        style={{
          display: "block",
          width: box.w,
          height: box.h,
          objectFit: "contain",
        }}
      />
      <Typography typography="s3" color="text.secondary" sx={NUM_SX}>
        {[
          item.name,
          item.width && item.height ? `${item.width} × ${item.height}` : null,
        ]
          .filter(Boolean)
          .join(" · ") || "Image"}
      </Typography>
    </Stack>
  );
}

Preview.propTypes = { item: PropTypes.object.isRequired };

export default function ImageStrip({ items, max = 3, size = 28 }) {
  const [open, setOpen] = useState(null);
  const shown = items.slice(0, max);
  const more = items.length - shown.length;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      data-image-strip={items.length}
      sx={{ minWidth: 0 }}
    >
      {shown.map((it, i) => (
        <CustomTooltip
          key={`${it.url.slice(0, 64)}-${i}`}
          show
          placement="right-start"
          enterDelay={250}
          title={<Preview item={it} />}
          slotProps={{
            tooltip: {
              sx: {
                bgcolor: "var(--bg-paper) !important",
                maxWidth: 260,
                p: 0,
              },
            },
          }}
        >
          <Box
            component="button"
            type="button"
            aria-label={`Open ${it.alt ?? it.name ?? `image ${i + 1}`}`}
            onClick={(e) => {
              e.stopPropagation();
              setOpen(i);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.stopPropagation();
            }}
            sx={{
              p: 0,
              width: size,
              height: size,
              flexShrink: 0,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              overflow: "hidden",
              cursor: "zoom-in",
              bgcolor: "background.neutral",
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
              },
            }}
          >
            <img
              src={it.url}
              alt={it.alt ?? it.name ?? `Image ${i + 1}`}
              loading="lazy"
              style={{
                display: "block",
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
          </Box>
        </CustomTooltip>
      ))}
      {more > 0 ? (
        <Box
          component="button"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setOpen(max);
          }}
          aria-label={`${more} more images`}
          sx={{
            p: 0,
            px: 0.5,
            border: 0,
            bgcolor: "transparent",
            cursor: "pointer",
            color: "text.secondary",
            typography: "s3",
          }}
        >
          {`+${more}`}
        </Box>
      ) : null}
      <MediaLightbox
        open={open != null}
        items={items}
        index={open ?? 0}
        onClose={() => setOpen(null)}
      />
    </Stack>
  );
}

ImageStrip.propTypes = {
  items: PropTypes.arrayOf(PropTypes.object).isRequired,
  max: PropTypes.number,
  size: PropTypes.number,
};
