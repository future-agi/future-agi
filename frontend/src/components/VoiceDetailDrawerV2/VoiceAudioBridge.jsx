import { useCallback, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import AudioPlayerCustom from "src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom";
import useVoiceAudioStore from "./voiceAudioStore";

/**
 * Wraps AudioPlayerCustom and publishes the wavesurfer-multitrack instance +
 * playback state to useVoiceAudioStore.
 *
 * Also owns the "fit-to-container" logic — wavesurfer-multitrack defaults
 * to minPxPerSec=10 which causes horizontal scroll for most calls. After
 * the tracks load, we compute pxPerSec = container_width / duration and
 * call multitrack.zoom(). A ResizeObserver re-fits on drawer resize.
 *
 * IMPORTANT: wavesurfer-multitrack does NOT emit audioprocess/seeking/play
 * events on its controller. Its internal playback is driven by a private
 * currentTime field; we poll multitrack.getCurrentTime() / isPlaying() via
 * our own rAF loop.
 */
const VoiceAudioBridge = ({ data }) => {
  const rafRef = useRef(null);
  const multitrackRef = useRef(null);
  const containerRef = useRef(null);
  const durationRef = useRef(0);
  const resizeObserverRef = useRef(null);

  const setInstance = useVoiceAudioStore((s) => s.setInstance);
  const setCurrentTime = useVoiceAudioStore((s) => s.setCurrentTime);
  const setDuration = useVoiceAudioStore((s) => s.setDuration);
  const setIsPlaying = useVoiceAudioStore((s) => s.setIsPlaying);
  const reset = useVoiceAudioStore((s) => s.reset);

  const stopPolling = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const disconnectResizeObserver = useCallback(() => {
    if (resizeObserverRef.current) {
      try {
        resizeObserverRef.current.disconnect();
      } catch {
        /* ignore */
      }
      resizeObserverRef.current = null;
    }
  }, []);

  // Compute pxPerSec so the full recording fits the current container width
  // and apply it via multitrack.zoom(). Guarded against missing duration and
  // repeated no-op zooms.
  const lastAppliedZoomRef = useRef(0);
  const fitToContainer = useCallback(() => {
    const mt = multitrackRef.current;
    const container = containerRef.current;
    const duration = durationRef.current;
    if (!mt || !container || !duration || duration <= 0) return;
    const width = container.offsetWidth;
    if (!width || width < 20) return;
    // Reserve ~24px for the scrollbar gutter and track labels
    const pxPerSec = Math.max(0.05, (width - 24) / duration);
    if (Math.abs(pxPerSec - lastAppliedZoomRef.current) < 0.02) return;
    lastAppliedZoomRef.current = pxPerSec;
    try {
      mt.zoom(pxPerSec);
    } catch {
      /* ignore — older versions may throw on zoom before ready */
    }
  }, []);

  const handleInstance = useCallback(
    ({ multitrack, wavesurfers }) => {
      stopPolling();
      disconnectResizeObserver();
      multitrackRef.current = multitrack;
      lastAppliedZoomRef.current = 0;
      setInstance({ multitrack, wavesurfers });
      if (!multitrack) {
        durationRef.current = 0;
        setDuration(0);
        setCurrentTime(0);
        setIsPlaying(false);
        return;
      }

      // Seed duration from the longest wavesurfer.
      let maxDur = 0;
      try {
        (wavesurfers || []).forEach((ws) => {
          const d = ws?.getDuration?.() ?? 0;
          if (d > maxDur) maxDur = d;
        });
      } catch {
        /* ignore */
      }
      durationRef.current = maxDur;
      if (maxDur > 0) setDuration(maxDur);

      // Initial fit. Run on the next frame so the container has settled its
      // layout after the loading placeholder is removed.
      requestAnimationFrame(() => fitToContainer());

      // Re-fit whenever the container resizes (drawer drag handle, window
      // resize, fullscreen toggle, etc).
      if (containerRef.current && typeof ResizeObserver !== "undefined") {
        resizeObserverRef.current = new ResizeObserver(() => {
          fitToContainer();
        });
        resizeObserverRef.current.observe(containerRef.current);
      }

      const master = wavesurfers?.[0];

      // rAF polling loop — read multitrack time + isPlaying, push to store.
      let lastPlaying = null;
      const tick = () => {
        const mt = multitrackRef.current;
        if (!mt) return;
        try {
          const mtTime = mt.getCurrentTime?.() ?? 0;
          const wsTime = master?.getCurrentTime?.() ?? 0;
          const t = Math.max(mtTime || 0, wsTime || 0);
          setCurrentTime(t);
          const playing = mt.isPlaying?.() ?? false;
          if (playing !== lastPlaying) {
            setIsPlaying(playing);
            lastPlaying = playing;
          }
        } catch {
          /* ignore read errors on teardown */
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    },
    [
      setInstance,
      setCurrentTime,
      setDuration,
      setIsPlaying,
      stopPolling,
      disconnectResizeObserver,
      fitToContainer,
    ],
  );

  useEffect(() => {
    return () => {
      stopPolling();
      disconnectResizeObserver();
      multitrackRef.current = null;
      durationRef.current = 0;
      lastAppliedZoomRef.current = 0;
      reset();
    };
  }, [reset, stopPolling, disconnectResizeObserver]);

  return (
    <Box ref={containerRef} sx={{ width: "100%", minWidth: 0 }}>
      <AudioPlayerCustom data={data} onInstance={handleInstance} />
    </Box>
  );
};

VoiceAudioBridge.propTypes = {
  data: PropTypes.object,
};

export default VoiceAudioBridge;
