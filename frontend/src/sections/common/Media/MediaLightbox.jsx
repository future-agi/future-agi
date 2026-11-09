// One lightbox for every media value (R4 lane M): images (arrows between
// them, zoom `+` / `−` / `0`), video (`<video controls>`), documents
// (`<object type="application/pdf">` + Open in new tab). `Esc` closes only
// this layer (MUI Dialog) and focus returns to the opener. Copy URL.
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CopyButton from "src/sections/common/ux/CopyButton";
import { NUM_SX } from "src/sections/common/format/fmt";
import { mediaLabel } from "./mediaRef";
import { fittedSize } from "./mediaModel";

const ZOOMS = [1, 1.5, 2, 3];

function ImageView({ item, zoom }) {
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 720;
  const box = fittedSize(item.width || 0, item.height || 0, vw - 160, vh - 180);
  const sized = item.width && item.height;
  return (
    <Box
      sx={{
        overflow: "auto",
        maxWidth: "calc(100vw - 120px)",
        maxHeight: "calc(100vh - 170px)",
      }}
    >
      <img
        src={item.url}
        alt={item.alt ?? item.name ?? "Image"}
        style={{
          display: "block",
          width: sized ? box.w * zoom : undefined,
          height: sized ? box.h * zoom : undefined,
          maxWidth: sized ? undefined : `calc(${zoom * 100}vw - 160px)`,
          maxHeight: sized ? undefined : `calc(${zoom * 100}vh - 180px)`,
        }}
      />
    </Box>
  );
}

ImageView.propTypes = {
  item: PropTypes.object.isRequired,
  zoom: PropTypes.number,
};

const urlOk = (u) => /^(https?:|\/|blob:)/i.test(String(u ?? ""));

export default function MediaLightbox({ open, items, index = 0, onClose }) {
  const list = (items ?? []).filter(Boolean);
  const [i, setI] = useState(index);
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    if (open) {
      setI(index);
      setZoom(1);
    }
  }, [open, index]);
  const item = list[Math.min(i, list.length - 1)];
  if (!item) return null;
  const many = list.length > 1;
  const go = (d) => {
    setI((x) => (x + d + list.length) % list.length);
    setZoom(1);
  };
  const onKeyDown = (e) => {
    if (e.target?.closest?.("input, textarea")) return;
    if (many && e.key === "ArrowLeft") {
      e.preventDefault();
      go(-1);
    } else if (many && e.key === "ArrowRight") {
      e.preventDefault();
      go(1);
    } else if (item.kind === "image" && (e.key === "+" || e.key === "=")) {
      setZoom(
        (z) => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)] ?? z,
      );
    } else if (item.kind === "image" && e.key === "-") {
      setZoom((z) => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)] ?? z);
    } else if (e.key === "0") setZoom(1);
  };
  const dims =
    item.width && item.height ? `${item.width} × ${item.height}` : null;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      onKeyDown={onKeyDown}
      PaperProps={{
        sx: {
          m: 2,
          maxWidth: "calc(100vw - 32px)",
          maxHeight: "calc(100vh - 32px)",
          bgcolor: "background.paper",
        },
      }}
      aria-label={mediaLabel(item)}
      data-media-lightbox={item.kind}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {item.name ?? mediaLabel(item)}
        </Typography>
        <Typography typography="s2" color="text.secondary" noWrap sx={NUM_SX}>
          {[dims, many ? `${i + 1} / ${list.length}` : null]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {item.kind === "image" ? (
          <Stack direction="row" alignItems="center">
            <IconButton
              size="small"
              aria-label="Zoom out"
              onClick={() =>
                setZoom((z) => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)] ?? z)
              }
              disabled={zoom <= 1}
            >
              <Iconify icon="mdi:magnify-minus-outline" width={18} />
            </IconButton>
            <Button
              size="small"
              color="inherit"
              onClick={() => setZoom(1)}
              sx={{ minWidth: 40, typography: "s3", ...NUM_SX }}
            >
              {zoom === 1 ? "Fit" : `${zoom}×`}
            </Button>
            <IconButton
              size="small"
              aria-label="Zoom in"
              onClick={() =>
                setZoom(
                  (z) =>
                    ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)] ??
                    z,
                )
              }
              disabled={zoom >= ZOOMS[ZOOMS.length - 1]}
            >
              <Iconify icon="mdi:magnify-plus-outline" width={18} />
            </IconButton>
          </Stack>
        ) : null}
        {urlOk(item.url) ? (
          <Button
            size="small"
            color="inherit"
            component="a"
            href={item.url}
            target="_blank"
            rel="noreferrer"
            startIcon={<Iconify icon="mdi:open-in-new" width={16} />}
            sx={{ typography: "s2" }}
          >
            Open in new tab
          </Button>
        ) : null}
        <CopyButton value={item.url} tooltip="Copy URL" />
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="center"
        gap={1}
        sx={{ p: 2, minWidth: 360, minHeight: 240 }}
      >
        {many ? (
          <IconButton aria-label="Previous" onClick={() => go(-1)}>
            <Iconify icon="mdi:chevron-left" width={24} />
          </IconButton>
        ) : null}
        {item.kind === "image" ? (
          <ImageView item={item} zoom={zoom} />
        ) : item.kind === "video" ? (
          <video
            src={item.url}
            controls
            preload="metadata"
            poster={item.poster_url}
            style={{
              maxWidth: "calc(100vw - 160px)",
              maxHeight: "calc(100vh - 180px)",
            }}
          >
            <track kind="captions" />
          </video>
        ) : item.kind === "document" || item.kind === "file" ? (
          <object
            data={item.url}
            type={
              item.mime === "application/pdf" ? "application/pdf" : undefined
            }
            aria-label={item.name ?? "Document"}
            style={{
              width: "min(900px, calc(100vw - 160px))",
              height: "calc(100vh - 180px)",
            }}
          >
            <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
              {
                "This browser can't show the document here. Use Open in new tab."
              }
            </Typography>
          </object>
        ) : (
          <Typography typography="s2">{mediaLabel(item)}</Typography>
        )}
        {many ? (
          <IconButton aria-label="Next" onClick={() => go(1)}>
            <Iconify icon="mdi:chevron-right" width={24} />
          </IconButton>
        ) : null}
      </Stack>
      {item.alt ? (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ px: 2, pb: 1.5 }}
        >
          {item.alt}
        </Typography>
      ) : null}
    </Dialog>
  );
}

MediaLightbox.propTypes = {
  open: PropTypes.bool,
  items: PropTypes.arrayOf(PropTypes.object),
  index: PropTypes.number,
  onClose: PropTypes.func.isRequired,
};
