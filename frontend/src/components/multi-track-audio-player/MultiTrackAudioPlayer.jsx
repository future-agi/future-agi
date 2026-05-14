import React, { useEffect, useRef, useCallback, useState, memo } from "react";
import MultiTrack from "wavesurfer-multitrack";
import PropTypes from "prop-types";
import { Icon } from "@iconify/react";
import { Box, IconButton, Stack, Typography, useTheme } from "@mui/material";
import { darkenColor } from "src/utils/utils";
import AudioDownloadButton from "src/sections/test-detail/AudioDownloadButton";
import { ShowComponent } from "../show";
import Iconify from "../iconify";

export const MemoizedBarsIcon = memo(() => (
  <Iconify icon="svg-spinners:bars-scale" width={20} height={20} />
));

MemoizedBarsIcon.displayName = "MemoizedBarsIcon";

const MultiTrackAudioPlayer = ({
  trackUrls,
  audioUrls,
  id,
  height = 50,
  allowDownload = true,
  onInstance,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const multiTrackAudioRef = useRef(null);
  const mtRef = useRef(null);
  const onInstanceRef = useRef(onInstance);
  const reportedInstanceRef = useRef(false);
  const [ready, setReady] = useState(0);
  const isReady = ready === trackUrls.length;

  // Keep latest onInstance in a ref so the instance callback fires with the
  // freshest handler without re-running the WaveSurfer init effect.
  useEffect(() => {
    onInstanceRef.current = onInstance;
  }, [onInstance]);

  const [isPlaying, setIsPlaying] = useState(false);
  useEffect(() => {
    if (!multiTrackAudioRef.current || trackUrls.length === 0) return;
    setReady(0);
    reportedInstanceRef.current = false;
    const tracks = trackUrls.map(({ url, color, name, peaks }, index) => ({
      id: `track-${index}`,
      url,
      peaks: peaks ? [peaks] : undefined,
      options: {
        waveColor: color || "#94A3B8",
        progressColor: darkenColor(color || "#94A3B8", 0.5, 0.5),
        height: height,
        barWidth: 2,
        barGap: 5,
        barHeight: 0.5,
        barRadius: 2,
      },
      name: `${name}`,
    }));

    mtRef.current = new MultiTrack(tracks, {
      container: multiTrackAudioRef.current,
      cursorColor: isDark ? "#fafafa" : "#0F172A",
      cursorWidth: 2,
      trackBackground: isDark ? "#18181b" : "#FFFFFF",

      minPxPerSec: 10,
      rightButtonDrag: true,
      dragBounds: true,
    });

    mtRef.current.on("canplay", () => {
      trackUrls.forEach((_, index) => {
        const currentWave = mtRef.current?.wavesurfers?.[index];
        currentWave?.on("ready", () => {
          setReady((prev) => prev + 1);
        });
      });
    });

    mtRef.current.initAllAudios();

    return () => {
      mtRef.current?.destroy();
      mtRef.current = null;
    };
  }, [trackUrls, height, isDark]);

  useEffect(() => {
    if (!isReady || reportedInstanceRef.current || !mtRef.current) return;
    reportedInstanceRef.current = true;

    // Hand the instance up to parents once every track is loaded. This runs
    // after render so parents can subscribe/seek without triggering React's
    // "setState while rendering another component" warning.
    onInstanceRef.current?.({
      multitrack: mtRef.current,
      wavesurfers: trackUrls.map((__, i) => mtRef.current?.wavesurfers?.[i]),
    });
  }, [isReady, trackUrls]);

  const togglePlay = useCallback(() => {
    if (!mtRef.current || !isReady) return;
    if (isPlaying) {
      mtRef.current.pause();
      setIsPlaying(false);
    } else {
      mtRef.current.play();
      setIsPlaying(true);
    }
  }, [isPlaying, isReady]);

  return (
    <Stack
      direction="column"
      gap={0}
      sx={{
        width: "100%",
        borderRadius: 0.5,
      }}
    >
      <Box
        sx={{
          position: "relative",
          minHeight: !isReady ? height * trackUrls.length + 20 : "auto",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {!isReady && (
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: "background.paper",
              zIndex: 10,
              gap: 1.5,
            }}
          >
            <MemoizedBarsIcon />
            <Typography typography="s1" fontWeight="fontWeightMedium">
              Painting sound waves...
            </Typography>
          </Box>
        )}
        <Box
          ref={multiTrackAudioRef}
          sx={{
            visibility: !isReady ? "hidden" : "visible",
            opacity: !isReady ? 0 : 1,
            transition: "opacity 0.3s ease-in-out",
            minHeight: 170,
          }}
        />
      </Box>

      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{
          width: "100%",
          paddingTop: 1.4,
        }}
      >
        <IconButton
          aria-label="play-pause"
          onClick={(event) => {
            event.stopPropagation();
            togglePlay();
          }}
          disabled={!isReady}
          sx={{
            padding: "6px",
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            opacity: isReady ? 1 : 0.5,
          }}
        >
          <Icon
            icon={isPlaying ? "lineicons:pause" : "akar-icons:play"}
            width={20}
            height={20}
            color="text.primary"
            style={{ pointerEvents: "none" }}
          />
        </IconButton>
        <ShowComponent condition={allowDownload && isReady}>
          <AudioDownloadButton
            audioUrls={{
              mono:
                audioUrls?.mono?.combinedUrl ||
                audioUrls?.combined ||
                (typeof audioUrls?.mono === "string" ? audioUrls.mono : ""),
              stereo: audioUrls?.stereoUrl || audioUrls?.stereo,
              assistant: audioUrls?.mono?.assistantUrl || audioUrls?.assistant,
              customer: audioUrls?.mono?.customerUrl || audioUrls?.customer,
            }}
            filename={`recording-${id || "audio"}.wav`}
            size="small"
            sx={{
              padding: "6px",
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              opacity: isReady ? 1 : 0.5,
            }}
          />
        </ShowComponent>
      </Stack>
    </Stack>
  );
};

export default MultiTrackAudioPlayer;

MultiTrackAudioPlayer.propTypes = {
  trackUrls: PropTypes.arrayOf(PropTypes.object),
  audioUrls: PropTypes.object,
  id: PropTypes.string,
  height: PropTypes.number,
  allowDownload: PropTypes.bool,
  onInstance: PropTypes.func,
};
