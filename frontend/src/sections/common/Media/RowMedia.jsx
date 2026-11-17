// Row view media block (dt-5): a media / call / persona value at
// `size="row"`, starting at most 240 px tall with `Expand` (images and
// documents are fitted to the height, a call view is clipped with a fade).
// Expanded, images fit 480 px and nothing is clipped. Personas are a chip.
import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import MediaValue from "./MediaValue";
import { columnMediaType, mediaCellKind } from "./mediaModel";
import { mediaRefOf } from "./mediaRef";

export const ROW_MEDIA_MAX = 240;
const OPEN_MAX = 480;
const CAPTION = 28; // the `640 × 480 · alt` line under a fitted image
// Kinds fitted to the block's height: they always grow when expanded.
const FITTED = new Set(["image", "video", "document"]);

export default function RowMedia({
  value,
  dataType,
  maxHeight = ROW_MEDIA_MAX,
}) {
  const kind = mediaCellKind(value, dataType);
  const ref =
    kind === "media"
      ? mediaRefOf(value, { columnType: columnMediaType(dataType) })
      : null;
  const fitted = FITTED.has((Array.isArray(ref) ? ref[0] : ref)?.kind);
  const innerRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [tall, setTall] = useState(false);
  useEffect(() => {
    const el = innerRef.current;
    if (!el) return undefined;
    const measure = () => setTall(el.scrollHeight > maxHeight + 4);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [maxHeight, kind]);
  if (kind === "persona") return <MediaValue value={value} size="row" />;
  const clip = !open && tall;
  return (
    <Box
      data-row-media={kind ?? "none"}
      sx={{ minWidth: 0, position: "relative" }}
    >
      <Box
        sx={{
          position: "relative",
          maxHeight: clip ? maxHeight : "none",
          overflow: clip ? "hidden" : "visible",
          ...(clip
            ? {
                maskImage:
                  "linear-gradient(to bottom, #000 calc(100% - 32px), transparent)",
              }
            : {}),
        }}
      >
        <Box ref={innerRef}>
          <MediaValue
            value={value}
            size="row"
            columnType={columnMediaType(dataType)}
            maxHeight={open ? OPEN_MAX : maxHeight - CAPTION}
          />
        </Box>
      </Box>
      {tall || open || fitted ? (
        <Button
          size="small"
          color="inherit"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          startIcon={
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={16}
            />
          }
          // Over the block's bottom-right corner, so the collapsed block
          // itself stays within its height.
          sx={{
            position: "absolute",
            right: 4,
            bottom: 4,
            zIndex: 1,
            typography: "s3",
            height: 24,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            "&:hover": { bgcolor: "background.paper" },
          }}
          data-row-media-expand=""
        >
          {open ? "Collapse" : "Expand"}
        </Button>
      ) : null}
    </Box>
  );
}

RowMedia.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  maxHeight: PropTypes.number,
};
