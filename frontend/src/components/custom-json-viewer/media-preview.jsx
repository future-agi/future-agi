import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Chip,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ClosableDialog } from "src/components/custom-dialog";
import { InlineAudio } from "src/components/inline-audio/inline-row-audio";
import { MEDIA_PREVIEW_CLASS, getSafeMediaSrc } from "./media-detection";

// Thumbnail that opens the full-size image.
export const ImagePreview = ({ value }) => {
  const [open, setOpen] = useState(false);
  const src = getSafeMediaSrc(value, "image");
  if (!src) return null;

  return (
    <>
      <ButtonBase
        className={MEDIA_PREVIEW_CLASS}
        aria-label="Open image preview"
        focusRipple
        onClick={() => setOpen(true)}
      >
        <Box
          component="img"
          src={src}
          alt="Image preview"
          loading="lazy"
          sx={{ width: 150, display: "block" }}
        />
      </ButtonBase>
      <ClosableDialog
        open={open}
        onClose={() => setOpen(false)}
        closeLabel="Close image preview"
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 960 } }}
      >
        <DialogContent sx={{ bgcolor: "background.paper" }}>
          <img src={src} alt="Full size image" style={{ width: "100%" }} />
        </DialogContent>
      </ClosableDialog>
    </>
  );
};

ImagePreview.propTypes = {
  value: PropTypes.string,
};

// Chip that opens an audio player. The audio only loads once opened.
export const AudioPreview = ({ value }) => {
  const [open, setOpen] = useState(false);
  const src = getSafeMediaSrc(value, "audio");
  if (!src) return null;

  return (
    <>
      <Chip
        className={MEDIA_PREVIEW_CLASS}
        size="small"
        variant="outlined"
        icon={<Iconify icon="solar:play-linear" width={14} />}
        label="Play audio"
        onClick={() => setOpen(true)}
      />
      <ClosableDialog
        open={open}
        onClose={() => setOpen(false)}
        closeLabel="Close audio player"
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Audio</DialogTitle>
        <DialogContent>
          <InlineAudio src={src} />
        </DialogContent>
      </ClosableDialog>
    </>
  );
};

AudioPreview.propTypes = {
  value: PropTypes.string,
};
