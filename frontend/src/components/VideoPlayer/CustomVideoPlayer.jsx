import React, { useRef, useState, useEffect } from "react";
import {
  MediaController,
  MediaControlBar,
  MediaTimeRange,
  MediaPlayButton,
  MediaMuteButton,
  MediaTimeDisplay,
} from "media-chrome/react";
import ReactPlayer from "react-player";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { Icon } from "@iconify/react";
import Image from "../image";

const CustomVideoPlayer = ({ videoSrc, thumbnail }) => {
  const theme = useTheme();
  const [showPlayer, setShowPlayer] = useState(false);
  const wrapperRef = useRef(null);
  const videoRef = useRef(null);
  const playTimeoutRef = useRef(null);

  const handleClickPreview = () => {
    setShowPlayer(true);

    const wrapper = wrapperRef.current;
    if (wrapper?.requestFullscreen) wrapper.requestFullscreen();
    else if (wrapper?.webkitRequestFullscreen)
      wrapper.webkitRequestFullscreen();
    else if (wrapper?.mozRequestFullScreen) wrapper.mozRequestFullScreen();
    else if (wrapper?.msRequestFullscreen) wrapper.msRequestFullscreen();

    playTimeoutRef.current = setTimeout(() => {
      videoRef.current?.play();
    }, 200);
  };

  const handleExitPlayer = () => {
    // Exit fullscreen
    if (document.exitFullscreen) document.exitFullscreen();
    else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
    else if (document.mozCancelFullScreen) document.mozCancelFullScreen();
    else if (document.msExitFullscreen) document.msExitFullscreen();

    // Pause and reset video
    videoRef.current?.pause();
    videoRef.current.currentTime = 0;
    setShowPlayer(false);
  };

  // Detect exit from fullscreen
  useEffect(() => {
    const handleFullscreenChange = () => {
      const isFullscreen =
        document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.mozFullScreenElement ||
        document.msFullscreenElement;

      if (!isFullscreen) {
        videoRef.current?.pause();
        videoRef.current.currentTime = 0;
        setShowPlayer(false);
      }
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    document.addEventListener("webkitfullscreenchange", handleFullscreenChange);
    document.addEventListener("mozfullscreenchange", handleFullscreenChange);
    document.addEventListener("MSFullscreenChange", handleFullscreenChange);

    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
      document.removeEventListener(
        "webkitfullscreenchange",
        handleFullscreenChange,
      );
      document.removeEventListener(
        "mozfullscreenchange",
        handleFullscreenChange,
      );
      document.removeEventListener(
        "MSFullscreenChange",
        handleFullscreenChange,
      );
      if (playTimeoutRef.current) {
        clearTimeout(playTimeoutRef.current);
        playTimeoutRef.current = null;
      }
    };
  }, []);

  return (
    <Box>
      <Box
        ref={wrapperRef}
        style={{
          width: "150px",
          height: "50px",
          position: "relative",
          borderRadius: "4px",
          overflow: "hidden",
          backgroundColor: "common.black",
        }}
      >
        {!showPlayer ? (
          <Box
            style={{
              width: "100%",
              height: "100%",
              cursor: "pointer",
              position: "relative",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "common.black",
            }}
            onClick={handleClickPreview}
          >
            {thumbnail ? (
              <Image
                src={thumbnail}
                alt="video thumbnail"
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                }}
              />
            ) : (
              <Box
                style={{ color: theme.palette.common.white, fontSize: "12px" }}
              >
                No thumbnail available
              </Box>
            )}
            {/* Play button overlay */}
            <Box
              style={{
                position: "absolute",
                top: "50%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                width: "25px",
                height: "25px",
                backgroundColor: theme.palette.background.paper,
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "common.white",
                fontSize: "12px",
              }}
            >
              <Icon
                icon={"solar:play-linear"}
                width={15}
                height={15}
                color={theme.palette.text.primary}
                style={{ pointerEvents: "none" }}
              />
            </Box>
          </Box>
        ) : (
          <MediaController style={{ width: "100%", height: "100%", gap: 0 }}>
            <ReactPlayer
              ref={videoRef}
              slot="media"
              src={videoSrc}
              style={{ width: "100%", height: "100%" }}
              playsInline
            />
            <MediaControlBar
              style={{
                width: "100%",
                backgroundColor: theme.palette.grey[900],
              }}
            >
              <Box
                display="flex"
                flexDirection="column"
                alignItems="stretch"
                width="100%"
                px={2}
                mb={1}
              >
                {/* Time Display */}
                <Box display="flex" justifyContent="space-between" px={1}>
                  <Typography alignContent={"flex-end"} fontSize={20}>
                    {/* Name */}
                  </Typography>
                  <MediaTimeDisplay
                    showDuration
                    style={{
                      background: "none",
                    }}
                  />
                </Box>

                {/* Time Range */}
                <Box width="100%">
                  <MediaTimeRange
                    style={{
                      width: "100%",
                      background: "none",
                    }}
                  />
                </Box>

                {/* Play + Mute + Exit Buttons */}
                <Box display="flex" justifyContent="space-between" gap={2}>
                  <MediaPlayButton
                    style={{
                      background: "none",
                    }}
                  />
                  <Box display="flex" gap={1}>
                    <MediaMuteButton
                      style={{
                        background: "none",
                      }}
                    />
                    {/* Exit button */}
                    <IconButton
                      onClick={handleExitPlayer}
                      aria-label="Exit fullscreen"
                      sx={{ color: "common.white" }}
                    >
                      <Icon
                        icon={"solar:quit-full-screen-linear"}
                        width={20}
                        height={20}
                        color="white"
                      />
                    </IconButton>
                  </Box>
                </Box>
              </Box>
            </MediaControlBar>
          </MediaController>
        )}
      </Box>
    </Box>
  );
};

CustomVideoPlayer.propTypes = {
  videoSrc: PropTypes.string,
  thumbnail: PropTypes.string,
};

export default CustomVideoPlayer;
