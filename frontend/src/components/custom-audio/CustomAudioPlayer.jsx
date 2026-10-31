import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
} from "react";
import WaveSurfer from "wavesurfer.js";
import { Icon } from "@iconify/react";
import { Box, IconButton, Skeleton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { formatTime, trimAudio } from "./audioHelper";
import { useAudioPlayback } from "./context-provider/useAudioPlayback";
import { getRandomId } from "src/utils/utils";
import logger from "src/utils/logger";
import { ShowComponent } from "../show";

const CustomAudioPlayer = ({
  audioData,
  showFileName = false,
  startTime = 0,
  endTime = null,
  activeColor = "",
  inactiveColor = "",
  pauseWhen,
  onAudioReady,
  onAudioError,
  splitTimeView = false,
  cacheKey,
  getWaveSurferInstance,
  storeWaveSurferInstance,
  updateWaveSurferInstance,
  customLoaderComponent,
}) => {
  // Resolve CSS variable values for canvas rendering (WaveSurfer needs actual color strings).
  // CSS variables work in both ThemeProvider and non-ThemeProvider contexts (e.g. Quill embeds).
  const getCSSVar = useCallback((varName, fallback) => {
    if (typeof document === "undefined") return fallback;
    return (
      getComputedStyle(document.documentElement)
        .getPropertyValue(varName)
        .trim() || fallback
    );
  }, []);
  const audioUrl = audioData?.url;
  const waveformRef = useRef(null);
  const wavesurfer = useRef(null);
  const abortControllerRef = useRef(null);
  const isInitializingRef = useRef(false);
  const { activePlayer, setActivePlayer } = useAudioPlayback();
  const [isLoading, setIsLoading] = useState(false);
  const [isError, setIsError] = useState(null);

  const playerId = useMemo(() => {
    return cacheKey || getRandomId();
  }, [cacheKey]);

  const isPlaying = activePlayer === playerId;

  // Fixed state initialization with proper boolean logic
  const [isMuted, setIsMuted] = useState(() => {
    if (cacheKey && getWaveSurferInstance) {
      const cacheData = getWaveSurferInstance(cacheKey);
      return cacheData?.isMuted != null ? cacheData.isMuted : false;
    }
    return false;
  });

  const [volume] = useState(1);

  const [currentTime, setCurrentTime] = useState(() => {
    if (cacheKey && getWaveSurferInstance) {
      const cacheData = getWaveSurferInstance(cacheKey);
      return cacheData?.currentTime != null ? cacheData.currentTime : 0;
    }
    return 0;
  });

  const [duration, setDuration] = useState(() => {
    if (cacheKey && getWaveSurferInstance) {
      const cacheData = getWaveSurferInstance(cacheKey);
      return cacheData?.duration != null ? cacheData.duration : 0;
    }
    return 0;
  });

  const previousVolume = useRef(volume);

  // Memoized callback to prevent unnecessary re-renders
  const setCurrentTimeEnhanced = useCallback(
    (time) => {
      setCurrentTime(time);
      if (cacheKey && updateWaveSurferInstance) {
        updateWaveSurferInstance(cacheKey, { currentTime: time });
      }
    },
    [cacheKey, updateWaveSurferInstance],
  );

  // Memoized play/pause handler
  const setIsPlaying = useCallback(
    (value, isPlaying = false) => {
      const isCurrentPlayer = isPlaying ? true : activePlayer === playerId;
      if (value) {
        wavesurfer?.current?.play();
      } else {
        wavesurfer?.current?.pause();
      }
      if (value) setActivePlayer(playerId);
      else if (!value && isCurrentPlayer) setActivePlayer(null);
    },
    [activePlayer, playerId, setActivePlayer],
  );

  // Handle global play state changes
  useEffect(() => {
    if (isPlaying) {
      wavesurfer?.current?.play();
    } else {
      wavesurfer?.current?.pause();
    }
  }, [isPlaying]);

  // Handle pause trigger
  useEffect(() => {
    if (wavesurfer.current && pauseWhen) {
      wavesurfer.current.pause();
    }
  }, [pauseWhen]);

  // Main initialization effect with fixed dependencies
  useEffect(() => {
    if (!audioUrl || startTime === null || isInitializingRef.current) return;

    // Create abort controller for this initialization
    abortControllerRef.current = new AbortController();
    const { signal } = abortControllerRef.current;

    const initializeWaveSurfer = async () => {
      if (signal.aborted) return;

      isInitializingRef.current = true;

      try {
        // Check cache first
        let cachedInstance = null;
        if (cacheKey && getWaveSurferInstance) {
          const cacheData = getWaveSurferInstance(cacheKey);
          cachedInstance = cacheData?.instance;
        }

        if (cachedInstance && !signal.aborted) {
          // Use cached instance
          wavesurfer.current = cachedInstance;

          // Ensure container is set correctly
          if (
            waveformRef.current &&
            wavesurfer.current.container !== waveformRef.current
          ) {
            wavesurfer.current.setOptions({ container: waveformRef.current });
          }

          // Clear existing listeners to prevent duplicates
          wavesurfer.current.unAll();

          // Re-attach listeners
          attachEventListeners(wavesurfer.current, signal);

          // Update component state
          const fullDuration = wavesurfer.current.getDuration();
          setDuration(endTime ?? fullDuration);
          setCurrentTimeEnhanced(
            startTime + wavesurfer.current.getCurrentTime(),
          );
          wavesurfer.current.setVolume(isMuted ? 0 : volume);

          if (!signal.aborted && onAudioReady) {
            onAudioReady();
          }
        } else if (!signal.aborted) {
          // Create new instance
          if (wavesurfer.current) {
            wavesurfer.current.destroy();
            wavesurfer.current = null;
          }

          const shouldTrim = startTime !== null && endTime !== null;
          const processedAudioUrl = shouldTrim
            ? await trimAudio(audioUrl, startTime, endTime)
            : audioData;

          if (signal.aborted) return;

          wavesurfer.current = WaveSurfer.create({
            container: waveformRef.current,
            height: 30,
            cursorWidth: 0,
            backend: "MediaElement",
            normalize: true,
            progressColor:
              // eslint-disable-next-line no-restricted-syntax -- ds: fallback colour
              activeColor || getCSSVar("--text-primary", "#201e25"),
            interact: true,
            renderFunction: (channels, ctx) => {
              const { width, height } = ctx.canvas;
              const scale = channels[0].length / width;
              const step = 5;
              const barWidth = 2;

              ctx.clearRect(0, 0, width, height);
              ctx.fillStyle =
                // eslint-disable-next-line no-restricted-syntax -- ds: fallback colour
                inactiveColor || getCSSVar("--border-default", "#e1dfec");
              ctx.translate(0, height / 2);

              for (let x = 0; x < width; x += step) {
                const index = Math.floor(x * scale);
                const value = Math.abs(channels[0][index]);
                const barHeight = value * height + 8;
                ctx.fillRect(x, -barHeight / 2, barWidth, barHeight);
              }

              ctx.resetTransform();
            },
          });

          // Attach event listeners
          attachEventListeners(wavesurfer.current, signal);

          // Load audio
          wavesurfer.current.load(processedAudioUrl?.url);
        }
      } catch (error) {
        if (!signal.aborted) {
          logger.error("WaveSurfer initialization error:", error);
          if (onAudioError) {
            onAudioError(error);
          }
        }
      } finally {
        isInitializingRef.current = false;
      }
    };

    // Event listener attachment helper
    const attachEventListeners = (waveSurferInstance, signal) => {
      waveSurferInstance.on("init", () => {
        if (!signal.aborted) {
          setIsLoading(true);
        }
      });

      waveSurferInstance.on("ready", () => {
        setIsError(null);
        if (signal.aborted) return;

        setIsLoading(false);
        const fullDuration = waveSurferInstance.getDuration();
        const finalDuration = endTime ?? fullDuration;
        setDuration(finalDuration);
        waveSurferInstance.setVolume(isMuted ? 0 : volume);
        setCurrentTimeEnhanced(startTime);

        // Store in cache if new instance
        if (
          cacheKey &&
          storeWaveSurferInstance &&
          !getWaveSurferInstance?.(cacheKey)
        ) {
          storeWaveSurferInstance(cacheKey, {
            instance: waveSurferInstance,
            duration: finalDuration,
            currentTime: startTime,
            isMuted: false,
          });
        }

        if (onAudioReady) {
          onAudioReady();
        }
      });

      waveSurferInstance.on("audioprocess", () => {
        if (signal.aborted) return;

        const current = waveSurferInstance.getCurrentTime();
        setCurrentTimeEnhanced(startTime + current);

        if (endTime && current >= endTime - startTime) {
          waveSurferInstance.pause();
        }
      });

      waveSurferInstance.on("seeking", () => {
        if (signal.aborted) return;
        setCurrentTimeEnhanced(startTime + waveSurferInstance.getCurrentTime());
      });

      waveSurferInstance.on("finish", () => {
        if (signal.aborted) return;
        setIsPlaying(false, true);
      });

      waveSurferInstance.on("error", (error) => {
        setIsError(error);
        setIsLoading(false);
        if (!signal.aborted && onAudioError) {
          onAudioError(error);
        }
      });
    };

    initializeWaveSurfer();

    // Cleanup function
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      if (wavesurfer.current) {
        const shouldKeepInCache = !!storeWaveSurferInstance && !!cacheKey;

        if (!shouldKeepInCache) {
          wavesurfer.current.destroy();
          wavesurfer.current = null;
        }
      }

      isInitializingRef.current = false;
    };
  }, [audioUrl, startTime, endTime, activeColor, inactiveColor, cacheKey]);

  const handlePlayPause = useCallback(() => {
    if (!wavesurfer.current) {
      logger.warn("WaveSurfer instance not available yet.");
      return;
    }
    setIsPlaying(!isPlaying);
  }, [isPlaying, setIsPlaying]);

  const handleMute = useCallback(() => {
    if (!wavesurfer.current) {
      logger.warn("WaveSurfer instance not available yet.");
      return;
    }

    if (!isMuted) {
      previousVolume.current = volume;
      wavesurfer.current.setVolume(0);
    } else {
      wavesurfer.current.setVolume(previousVolume.current);
    }

    if (cacheKey && updateWaveSurferInstance) {
      updateWaveSurferInstance(cacheKey, { isMuted: !isMuted });
    }

    setIsMuted((prev) => !prev);
  }, [isMuted, volume, cacheKey, updateWaveSurferInstance]);

  return (
    <Box
      className="audio-control-btn"
      display="flex"
      flexDirection="column"
      width="100%"
      position="relative"
      onMouseDown={(event) => event.stopPropagation()}
    >
      {showFileName && (
        <Typography sx={{ fontSize: "13px", ml: "5px", color: "text.primary" }}>
          {audioData?.fileName}
        </Typography>
      )}
      <ShowComponent condition={isError}>
        <Typography typography="s1" color="status.danger.fg">
          {isError?.message}
        </Typography>
      </ShowComponent>
      <ShowComponent condition={!isError}>
        <Box display="flex" alignItems="center" width="100%" sx={{ gap: 0.5 }}>
          <IconButton
            aria-label="play-pause"
            onClick={(event) => {
              event.stopPropagation();
              handlePlayPause();
            }}
          >
            <Icon
              icon={isPlaying ? "solar:pause-linear" : "solar:play-linear"}
              width={20}
              height={20}
              color="var(--text-primary)"
              style={{ pointerEvents: "none" }}
            />
          </IconButton>

          {isLoading &&
            (customLoaderComponent || (
              <Skeleton
                sx={{ width: "100%", height: "20px" }}
                variant="rounded"
              />
            ))}

          <Box
            ref={waveformRef}
            sx={{
              flexGrow: 1,
              borderRadius: 2,
              overflow: "hidden",
              display: isLoading ? "none" : "block",
            }}
            // Prevent clicks on the waveform for calling parent function when seeking
            onClick={(event) => event.stopPropagation()}
          />

          {/* Time Display */}
          <Typography
            variant="body2"
            sx={{
              textAlign: "center",
              fontSize: "12px",
              ml: "5px",
              display: isLoading ? "none" : "block",
            }}
          >
            {!splitTimeView && (
              <Box
                component="span"
                sx={{
                  color: "var(--text-disabled)",
                  textAlign: "center",
                  fontSize: "12px",
                  mr: "5px",
                }}
              >
                {formatTime(currentTime)} {" / "}
              </Box>
            )}
            <Box
              component="span"
              sx={{
                color: splitTimeView
                  ? "var(--text-primary)"
                  : "var(--text-disabled)",
              }}
            >
              {formatTime(duration)}
            </Box>
          </Typography>

          {/* Mute Button */}
          <IconButton
            className="audio-control-btn"
            aria-label="mute-unmute"
            onClick={(event) => {
              event.stopPropagation();
              handleMute();
            }}
          >
            <Icon
              icon={
                isMuted
                  ? "solar:volume-cross-linear"
                  : "solar:volume-loud-linear"
              }
              width={22}
              height={22}
              color="var(--text-primary)"
              style={{ pointerEvents: "none" }}
            />
          </IconButton>
        </Box>
      </ShowComponent>
    </Box>
  );
};

CustomAudioPlayer.propTypes = {
  audioData: PropTypes.object,
  showFileName: PropTypes.bool,
  showDetails: PropTypes.bool,
  startTime: PropTypes.number,
  endTime: PropTypes.number,
  activeColor: PropTypes.string,
  inactiveColor: PropTypes.string,
  pauseWhen: PropTypes.string,
  onAudioReady: PropTypes.func,
  onAudioError: PropTypes.func,
  splitTimeView: PropTypes.bool,
  cacheKey: PropTypes.string,
  getWaveSurferInstance: PropTypes.func,
  storeWaveSurferInstance: PropTypes.func,
  updateWaveSurferInstance: PropTypes.func,
  customLoaderComponent: PropTypes.node,
};

export default CustomAudioPlayer;
