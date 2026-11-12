// A document in a cell: `contract.pdf · 4 pages` (or the size); click opens
// the viewer (lightbox with `<object type="application/pdf">`).
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { fmtNumber } from "src/sections/common/format/fmt";
import MediaLightbox from "./MediaLightbox";

const MB = 1024 * 1024;
const kb = (bytes) =>
  Number.isFinite(bytes)
    ? bytes < MB
      ? `${fmtNumber(Math.max(1, Math.round(bytes / 1024)))} KB`
      : `${fmtNumber(bytes / MB)} MB`
    : null;

/** `contract.pdf · 4 pages` / `contract.pdf · 12 KB` / `contract.pdf`. */
export function docChipText(ref) {
  const name =
    ref.name ?? (ref.mime === "application/pdf" ? "PDF" : "Document");
  const detail = Number.isFinite(ref.pages)
    ? `${ref.pages} ${ref.pages === 1 ? "page" : "pages"}`
    : kb(ref.size_bytes);
  return detail ? `${name} · ${detail}` : name;
}

export default function DocChip({ mediaRef }) {
  const [open, setOpen] = useState(false);
  const pdf = mediaRef.mime === "application/pdf";
  return (
    <>
      <Stack
        component="button"
        type="button"
        direction="row"
        alignItems="center"
        gap={0.5}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        aria-label={`Open ${docChipText(mediaRef)}`}
        data-doc-chip=""
        sx={{
          px: 0.75,
          py: 0.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          bgcolor: "background.paper",
          cursor: "pointer",
          minWidth: 0,
          maxWidth: "100%",
          color: "text.primary",
        }}
      >
        <Iconify
          icon={pdf ? "mdi:file-pdf-box" : "mdi:file-document-outline"}
          width={16}
          sx={{ color: pdf ? "red.600" : "text.secondary", flexShrink: 0 }}
        />
        <Typography typography="s3" noWrap>
          {docChipText(mediaRef)}
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

DocChip.propTypes = { mediaRef: PropTypes.object.isRequired };
