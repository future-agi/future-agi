import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
} from "react";
import WaveSurfer from "wavesurfer.js";
import { Icon } from "@iconify/react";
import {
  Box,
  IconButton,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { formatTime } from "./audioHelper";
import { useAudioPlayback } from "./context-provider/useAudioPlayback";
import { getRandomId } from "src/utils/utils";
import logger from "src/utils/logger";
import { ShowComponent } from "../show";
import { getCachedValue } from "./common";
import Iconify from "../iconify";
import DummyWaveform from "../DummyWaveform";

const ERROR_MESSAGE = "Audio failed to load. Try reloading.";

const TestAudioPlayer = ({
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
  const theme = useTheme();
  const audioUrl = audioData?.url;
  const waveformRef = useRef(null);
  const wavesurfer = useRef(null);
  const abortControllerRef = useRef(null);
  const isInitializingRef = useRef(false);
  const { activePlayer, setActivePlayer } = useAudioPlayback();
  const [isLoading, setIsLoading] = useState(false);
  const [isError, setIsError] = useState(null);
  const previousVolume = useRef(1);
  const eventListenersRef = useRef([]);

  const [initialized, setInitialized] = useState(() =>
    getCachedValue(cacheKey, getWaveSurferInstance, "initialized", false),
  );

  const updateInitState = useCallback(
    (value) => {
      // Update React state
      setInitialized(value);

      // Update Map ref
      if (cacheKey && updateWaveSurferInstance) {
        updateWaveSurferInstance(cacheKey, { initialized: value });
      }
    },
    [cacheKey, updateWaveSurferInstance],
  );
  // Fixed state initialization with proper boolean logic
  const [isMuted, setIsMuted] = useState(() =>
    getCachedValue(cacheKey, getWaveSurferInstance, "isMuted", false),
  );

  const [currentTime, setCurrentTime] = useState(() =>
    getCachedValue(cacheKey, getWaveSurferInstance, "currentTime", 0),
  );

  const [duration, setDuration] = useState(() =>
    getCachedValue(cacheKey, getWaveSurferInstance, "duration", 0),
  );

  const playerId = useMemo(() => {
    return cacheKey || getRandomId();
  }, [cacheKey]);

  const isPlaying = activePlayer === playerId;

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

  // Event listener attachment helper
  const attachEventListeners = useCallback(
    (waveSurferInstance, signal) => {
      try {
        const handlers = {
          init: () => {
            if (!signal.aborted) {
              setIsLoading(true);
            }
          },
          ready: () => {
            setIsError(null);
            if (signal.aborted) return;

            setIsLoading(false);
            const fullDuration = waveSurferInstance.getDuration();
            const finalDuration = endTime ?? fullDuration;
            setDuration(finalDuration);
            waveSurferInstance.setVolume(isMuted ? 0 : previousVolume.current);
            setCurrentTimeEnhanced(startTime);

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
                initialized: true,
              });
            }

            if (onAudioReady) {
              onAudioReady();
            }
          },
          audioprocess: () => {
            if (signal.aborted) return;

            const current = waveSurferInstance.getCurrentTime();
            setCurrentTimeEnhanced(startTime + current);

            if (endTime && current >= endTime - startTime) {
              // Seek back to endtime before pausing
              const fullDuration = waveSurferInstance.getDuration();
              const finDuration =
                endTime > fullDuration ? fullDuration : endTime;
              waveSurferInstance.seekTo(finDuration);
              setCurrentTimeEnhanced(finDuration);
              setIsPlaying(false, true);
            }
          },
          seeking: () => {
            if (signal.aborted) return;
            setCurrentTimeEnhanced(
              startTime + waveSurferInstance.getCurrentTime(),
            );
          },
          finish: () => {
            if (signal.aborted) return;
            setIsPlaying(false, true);
          },
          error: (error) => {
            setIsError({
              message: ERROR_MESSAGE,
            });
            updateInitState(false);
            setIsLoading(false);
            if (!signal.aborted && onAudioError) {
              onAudioError(error);
            }
          },
        };

        // Attach listeners and track them for cleanup
        Object.entries(handlers).forEach(([event, handler]) => {
          waveSurferInstance.on(event, handler);
          eventListenersRef.current.push({ event, handler });
        });
      } catch (error) {
        updateInitState(false);
        setIsError({
          message: ERROR_MESSAGE,
        });
      }
    },
    [
      cacheKey,
      endTime,
      getWaveSurferInstance,
      isMuted,
      onAudioError,
      onAudioReady,
      setCurrentTimeEnhanced,
      setIsPlaying,
      startTime,
      storeWaveSurferInstance,
    ],
  );

  const initWaveSurfer = useCallback(async () => {
    if (initialized || !audioUrl) return;

    abortControllerRef.current = new AbortController();
    const { signal } = abortControllerRef.current;
    isInitializingRef.current = true;
    try {
      if (signal.aborted) return;
      updateInitState(true);

      wavesurfer.current = WaveSurfer.create({
        container: waveformRef.current,
        height: 30,
        cursorWidth: 0,
        backend: "MediaElement",
        normalize: true,
        progressColor: activeColor || theme.palette.text.secondary,
        interact: true,
        renderFunction: (channels, ctx) => {
          const { width, height } = ctx.canvas;
          const scale = channels[0].length / width;
          const step = 5;
          const barWidth = 2;

          ctx.clearRect(0, 0, width, height);
          ctx.fillStyle = inactiveColor || theme.palette.divider;
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

      attachEventListeners(wavesurfer.current, signal);

      wavesurfer.current.load(audioUrl);

      // Auto-start playback after ready
      wavesurfer.current.once("ready", () => {
        setIsLoading(false);
        wavesurfer.current.play();
        setActivePlayer(playerId);
      });
    } catch (e) {
      updateInitState(false);
      setIsError({
        message: ERROR_MESSAGE,
      });
      logger.error(e);
      updateInitState(false);
      if (!signal.aborted) {
        if (onAudioError) {
          onAudioError(e);
        }
      }
    } finally {
      isInitializingRef.current = false;
      setIsLoading(false);
    }
  }, [
    initialized,
    audioUrl,
    activeColor,
    attachEventListeners,
    updateInitState,
    inactiveColor,
    setActivePlayer,
    playerId,
  ]);

  // Helper to remove all event listeners
  const removeEventListeners = useCallback(() => {
    if (wavesurfer?.current && eventListenersRef?.current?.length > 0) {
      eventListenersRef?.current?.forEach(({ event, handler }) => {
        wavesurfer?.current?.un(event, handler);
      });
      eventListenersRef.current = [];
    }
  }, []);

  useEffect(() => {
    if (!audioUrl || isInitializingRef.current) return;

    // Check cache first
    abortControllerRef.current = new AbortController();
    const { signal } = abortControllerRef.current;

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
      setCurrentTimeEnhanced(startTime + wavesurfer.current.getCurrentTime());
      wavesurfer.current.setVolume(isMuted ? 0 : previousVolume.current);

      if (!signal.aborted && onAudioReady) {
        onAudioReady();
      }
    }

    // Cleanup function
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      if (wavesurfer.current) {
        const shouldKeepInCache = !!storeWaveSurferInstance && !!cacheKey;

        if (!shouldKeepInCache) {
          wavesurfer.current?.destroy();
          wavesurfer.current = null;
          removeEventListeners();
        }
      }

      isInitializingRef.current = false;
    };
  }, [cacheKey]);

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
      wavesurfer.current?.pause();
    }
  }, [pauseWhen]);

  const handlePlayPause = useCallback(() => {
    if (!initialized) {
      updateInitState(true);
      initWaveSurfer();
      return;
    }

    // If trying to play after reaching endTime, reset to startTime
    if (!isPlaying && wavesurfer.current) {
      const currentTime = wavesurfer.current.getCurrentTime();
      const fullDuration = wavesurfer.current.getDuration();
      const absoluteTime = startTime + currentTime;

      // If at or past endTime, seek back to startTime for replay
      if (endTime && absoluteTime >= endTime) {
        const startSeekPosition = startTime / fullDuration;
        wavesurfer.current.seekTo(startSeekPosition);
        setCurrentTimeEnhanced(startTime);
      }
    }

    setIsPlaying(!isPlaying);
  }, [
    initialized,
    setIsPlaying,
    isPlaying,
    initWaveSurfer,
    updateInitState,
    startTime,
    endTime,
    setCurrentTimeEnhanced,
  ]);

  const handleMute = useCallback(() => {
    if (!wavesurfer.current) {
      logger.warn("WaveSurfer instance not available yet.");
      return;
    }

    if (!isMuted) {
      previousVolume.current = 1;
      wavesurfer?.current?.setVolume(0);
    } else {
      wavesurfer?.current?.setVolume(previousVolume.current);
    }

    if (cacheKey && updateWaveSurferInstance) {
      updateWaveSurferInstance(cacheKey, { isMuted: !isMuted });
    }

    setIsMuted((prev) => !prev);
  }, [isMuted, cacheKey, updateWaveSurferInstance]);

  const handleReload = useCallback(
    (e) => {
      try {
        e.stopPropagation();
        e.preventDefault();

        // Abort any ongoing initialization
        if (abortControllerRef.current) {
          abortControllerRef.current.abort();
        }

        // Reset error state
        setIsError(null);

        // Reset initialized state
        updateInitState(false);
        setInitialized(false);
        isInitializingRef.current = false;
        // Clean up existing instance
        if (wavesurfer.current) {
          removeEventListeners();
          wavesurfer.current.destroy();
          wavesurfer.current = null;
        }

        setTimeout(() => {
          initWaveSurfer();
        }, 100);
      } catch (error) {
        setIsError({
          message: ERROR_MESSAGE,
        });
      }
    },
    [removeEventListeners, updateInitState, initWaveSurfer],
  );

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
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          gap={1.5}
          sx={{
            pl: 1.25,
            pr: 0.5,
            py: 0.75,
            backgroundColor: "error.lighter", // or 'rgba(255, 59, 48, 0.08)'
            borderRadius: 1,
            border: "1px solid",
            borderColor: "error.light", // or 'rgba(255, 59, 48, 0.2)'
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            flex={1}
            minWidth={0}
          >
            <Iconify
              icon="solar:danger-circle-linear"
              sx={{
                width: 16,
                height: 16,
                color: "error.main",
                flexShrink: 0,
              }}
            />
            <Typography
              typography="s2"
              color="error.dark"
              sx={{
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontWeight: 500,
              }}
            >
              {isError?.message || "Audio failed to load"}
            </Typography>
          </Stack>

          <IconButton
            onClick={handleReload}
            onMouseDown={(event) => {
              event.stopPropagation();
              // @ts-ignore
              window.__audioClick = true;
            }}
            size="small"
            title="Reload"
            sx={{
              flexShrink: 0,
              "&:hover": {
                backgroundColor: "error.lighter",
              },
            }}
          >
            <Iconify
              icon="solar:restart-bold"
              sx={{
                width: 14,
                height: 14,
                color: "error.main",
              }}
            />
          </IconButton>
        </Stack>
      </ShowComponent>
      <ShowComponent condition={!isError}>
        <Box display="flex" alignItems="center" width="100%" sx={{ gap: 0.5 }}>
          <IconButton
            aria-label="play-pause"
            onClick={(event) => {
              if (isInitializingRef.current) return;
              event.stopPropagation();
              event.preventDefault();
              handlePlayPause();
            }}
            onMouseDown={(event) => {
              event.stopPropagation();
            }}
            sx={{ color: "text.primary" }}
            disabled={isLoading}
          >
            <Icon
              icon={isPlaying ? "solar:pause-linear" : "solar:play-linear"}
              width={20}
              height={20}
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
          {!initialized && !isLoading && <DummyWaveform />}
          <Box
            ref={waveformRef}
            sx={{
              flexGrow: 1,
              borderRadius: 2,
              overflow: "hidden",
            }}
            onClick={(event) => {
              event.stopPropagation();
              // @ts-ignore
              window.__audioClick = true;
            }}
            onMouseDown={(event) => {
              event.stopPropagation();
              // @ts-ignore
              window.__audioClick = true;
            }}
          />

          <ShowComponent condition={!!initialized}>
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
                    color: theme.palette.text.disabled,
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
                    ? theme.palette.text.primary
                    : theme.palette.text.disabled,
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
                event.preventDefault();
                // @ts-ignore
                window.__audioClick = true;
                handleMute();
              }}
              onMouseDown={(event) => {
                event.stopPropagation();
                // @ts-ignore
                window.__audioClick = true;
              }}
              sx={{ color: "text.primary" }}
              disabled={isLoading}
            >
              <Icon
                icon={
                  isMuted
                    ? "solar:volume-cross-linear"
                    : "solar:volume-loud-linear"
                }
                width={22}
                height={22}
                style={{ pointerEvents: "none" }}
              />
            </IconButton>
          </ShowComponent>
        </Box>
      </ShowComponent>
    </Box>
  );
};

TestAudioPlayer.propTypes = {
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

export default React.memo(TestAudioPlayer);
