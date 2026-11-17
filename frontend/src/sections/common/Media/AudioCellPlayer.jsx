// An audio value in a grid cell: MiniWaveform + duration + ▶ (shown on
// hover and on focus). Plays on click only, never on hover; one audio at a
// time app-wide through audioBus; leaving the page (unmount) stops it.
import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { NUM_SX } from "src/sections/common/format/fmt";
import { claimAudio, releaseAudio } from "./audioBus";
import { clockOf } from "./mediaModel";
import MiniWaveform from "./MiniWaveform";

let seq = 0;

export default function AudioCellPlayer({
  mediaRef,
  width = 80,
  dense = false,
}) {
  const idRef = useRef(null);
  if (!idRef.current) {
    seq += 1;
    idRef.current = `audio-cell-${seq}`;
  }
  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const [durationMs, setDurationMs] = useState(mediaRef?.duration_ms ?? null);

  useEffect(
    () => () => {
      audioRef.current?.pause();
      releaseAudio(idRef.current);
    },
    [],
  );

  const stop = () => {
    audioRef.current?.pause();
    setPlaying(false);
  };

  const toggle = (e) => {
    e.stopPropagation();
    if (playing) {
      stop();
      releaseAudio(idRef.current);
      return;
    }
    if (!audioRef.current) {
      const a = new Audio(mediaRef.url);
      a.preload = "auto";
      a.addEventListener("timeupdate", () => {
        if (a.duration) setProgress(a.currentTime / a.duration);
      });
      a.addEventListener("loadedmetadata", () => {
        if (Number.isFinite(a.duration)) setDurationMs(a.duration * 1000);
      });
      a.addEventListener("ended", () => {
        setPlaying(false);
        setProgress(0);
        releaseAudio(idRef.current);
      });
      a.addEventListener("error", () => {
        setFailed(true);
        setPlaying(false);
        releaseAudio(idRef.current);
      });
      audioRef.current = a;
    }
    claimAudio(idRef.current, stop);
    audioRef.current
      .play()
      .then(() => setPlaying(true))
      .catch(() => {
        setPlaying(false);
        releaseAudio(idRef.current);
      });
  };

  const label = failed
    ? "This recording could not be played"
    : playing
      ? "Pause"
      : `Play ${mediaRef?.name ?? "audio"} (click; never on hover)`;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-audio-cell={playing ? "playing" : "paused"}
      sx={{
        minWidth: 0,
        "& .fa-audio-play": { opacity: playing ? 1 : 0 },
        "&:hover .fa-audio-play, & .fa-audio-play:focus-visible": {
          opacity: 1,
        },
      }}
    >
      <CustomTooltip show size="small" title={label}>
        <IconButton
          size="small"
          className="fa-audio-play"
          aria-label={playing ? "Pause audio" : "Play audio"}
          onClick={toggle}
          disabled={failed}
          sx={{
            p: 0.25,
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon={
              failed ? "mdi:alert-outline" : playing ? "mdi:pause" : "mdi:play"
            }
            width={14}
          />
        </IconButton>
      </CustomTooltip>
      {dense ? (
        <Iconify
          icon="mdi:waveform"
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      ) : (
        <Box onClick={toggle} sx={{ cursor: "pointer", flexShrink: 0 }}>
          <MiniWaveform
            peaks={mediaRef?.peaks}
            width={width}
            height={20}
            progress={progress}
          />
        </Box>
      )}
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ ...NUM_SX, flexShrink: 0 }}
      >
        {clockOf(durationMs)}
      </Typography>
    </Stack>
  );
}

AudioCellPlayer.propTypes = {
  mediaRef: PropTypes.object.isRequired,
  width: PropTypes.number,
  dense: PropTypes.bool,
};
