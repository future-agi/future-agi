import React, { useEffect, useRef, useCallback, useState, memo } from "react";
import MultiTrack from "wavesurfer-multitrack";
import PropTypes from "prop-types";
import { Icon } from "@iconify/react";
import { Box, IconButton, Stack, Typography, useTheme } from "@mui/material";
import { darkenColor } from "src/utils/utils";
import AudioDownloadButton from "src/sections/test-detail/AudioDownloadButton";
import RecordingFailure from "./RecordingFailure";
import { UNAVAILABLE, LOAD_FAILED } from "./failureVariants";
import { ShowComponent } from "../show";
import Iconify from "../iconify";

export const MemoizedBarsIcon = memo(() => (
  <Iconify icon="svg-spinners:bars-scale" width={20} height={20} />
));

MemoizedBarsIcon.displayName = "MemoizedBarsIcon";

// MediaError codes worth telling apart; the rest mean the source itself
// could not be used. https://developer.mozilla.org/docs/Web/API/MediaError
const MEDIA_ERR_NETWORK = 2;
const MEDIA_ERR_DECODE = 3;

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
  const [failure, setFailure] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const isReady = ready === trackUrls.length && !failure;

  // Keep latest onInstance in a ref so the instance callback fires with the
  // freshest handler without re-running the WaveSurfer init effect.
  useEffect(() => {
    onInstanceRef.current = onInstance;
  }, [onInstance]);

  const [isPlaying, setIsPlaying] = useState(false);
  useEffect(() => {
    if (!multiTrackAudioRef.current || trackUrls.length === 0) return;
    setReady(0);
    setFailure(null);
    reportedInstanceRef.current = false;

    // No URL is not a player failure — it means the call has no recording, and
    // the caller renders its own "No recording found" for that. This component
    // only reports recordings that exist but cannot be played.
    if (trackUrls.some(({ url }) => !url)) {
      return undefined;
    }

    // The player owns one media element per track and hands it to the track
    // below. wavesurfer-multitrack emits no error event, and when a track's
    // media never loads it leaves `wavesurfers` empty forever, so an element
    // we own is the only thing that can report that failure.
    // "metadata" is enough to surface a refused source, and wavesurfer fetches
    // the file itself anyway — "auto" would download every recording twice.
    const medias = trackUrls.map(({ url }) => {
      const media = new Audio();
      media.preload = "metadata";
      media.src = url;
      return media;
    });

    const onMediaError = (media) => () => {
      const code = media.error?.code;
      // A source the server refused (403/404/CORS) is a recording we cannot
      // reach at all; a dropped download or a file we cannot decode is worth
      // another attempt. First failure wins, so two tracks failing for
      // different reasons cannot flip the variant on arrival order.
      setFailure(
        (prev) =>
          prev ??
          (code === MEDIA_ERR_NETWORK || code === MEDIA_ERR_DECODE
            ? LOAD_FAILED
            : UNAVAILABLE),
      );
    };
    const mediaErrorHandlers = medias.map((media) => {
      const handler = onMediaError(media);
      media.addEventListener("error", handler);
      return handler;
    });

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
        media: medias[index],
      },
      name: `${name}`,
    }));

    const multitrack = new MultiTrack(tracks, {
      container: multiTrackAudioRef.current,
      cursorColor: isDark ? "#fafafa" : "#0F172A",
      cursorWidth: 2,
      trackBackground: isDark ? "#18181b" : "#FFFFFF",

      rightButtonDrag: true,
      dragBounds: true,
    });

    mtRef.current = multitrack;

    // The wavesurfers only exist once the multitrack reports canplay, so the
    // success path has to wait for it. A track whose media never loads never
    // gets this far — that is what the media listeners above are for.
    //
    // Bound to `multitrack`, never `mtRef.current`: destroy() leaves its own
    // listeners attached, so a torn-down instance can still emit canplay and
    // would otherwise subscribe a second set of "ready" handlers to whatever
    // instance is current, pushing the count past the total and stranding the
    // loader.
    multitrack.on("canplay", () => {
      trackUrls.forEach((_, index) => {
        const currentWave = multitrack.wavesurfers?.[index];
        currentWave?.on("ready", () => {
          setReady((prev) => prev + 1);
        });
        // The media element loading is not the whole story: wavesurfer fetches
        // and decodes the file a second time for the waveform, and that pass
        // can fail on a source the element played happily — a signed URL that
        // expired between the two requests, a transient 5xx, a container
        // decodeAudioData refuses. Without this the run ends with neither
        // "ready" nor a media error, which is the endless loader all over.
        currentWave?.on("error", () => {
          setFailure((prev) => prev ?? LOAD_FAILED);
        });
      });
    });

    multitrack.initAllAudios();

    return () => {
      // Listeners come off before destroy(), which sets src = "" on every
      // element — that resolves against the document URL and would otherwise
      // fire a spurious "source refused" error per track. Do not reorder.
      medias.forEach((media, index) => {
        media.removeEventListener("error", mediaErrorHandlers[index]);
      });
      multitrack.destroy();
      mtRef.current = null;
    };
    // `attempt` is the retry lever: bumping it tears the tracks down and
    // rebuilds them from scratch.
  }, [trackUrls, height, isDark, attempt]);

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
          // A failure occupies exactly the footprint the waveform would have
          // had, so nothing below it shifts. The drawer that hosts this is what
          // decides where that block sits vertically.
          ...(failure
            ? { height: height * trackUrls.length + 20 }
            : { minHeight: !isReady ? height * trackUrls.length + 20 : "auto" }),
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {failure && (
          <RecordingFailure
            variant={failure}
            onRetry={() => setAttempt((n) => n + 1)}
          />
        )}
        {!isReady && !failure && (
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
            // The hidden container reserves 170px, which would prop the
            // failure box open past the footprint above.
            minHeight: failure ? 0 : 170,
            height: failure ? 0 : undefined,
          }}
        />
      </Box>

      {/* Nothing to play and nothing to download once a track has failed, so
          the whole transport goes rather than leaving a dead play button
          beside the failure message. */}
      {!failure && (
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
      )}
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
