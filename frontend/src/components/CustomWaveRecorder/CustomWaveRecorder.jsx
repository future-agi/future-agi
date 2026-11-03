import { Box, IconButton, Typography, useTheme } from "@mui/material";
import React, { useContext, useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import RecordPlugin from "wavesurfer.js/dist/plugins/record.esm.js";
import { ShowComponent } from "../show";
import SvgColor from "../svg-color";
import CustomWaveRecorderContext from "./CustomWaveRecorderContext";
import { useSnackbar } from "src/components/snackbar";
import PropTypes from "prop-types";
import logger from "src/utils/logger";

const CustomWaveRecorder = ({ onClose }) => {
  const [wavesurfer, setWavesurfer] = useState(null);
  const [record, setRecord] = useState(null);
  const { isRecording, setIsRecording, setRecordedAudio } = useContext(
    CustomWaveRecorderContext,
  );
  const [isPaused, setIsPaused] = useState(false);
  const [recordingTime, setRecordingTime] = useState("00:00");
  const { enqueueSnackbar } = useSnackbar();
  const [isMicrophonePermissionGranted, setIsMicrophonePermissionGranted] =
    useState(true);

  const theme = useTheme();
  const waveformRef = useRef(null);

  const requestMicrophonePermission = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop()); // Clean up the stream
      return true;
    } catch (error) {
      logger.error("Microphone permission error:", error);
      setIsMicrophonePermissionGranted(false);
      enqueueSnackbar("Please allow microphone access to record audio.", {
        variant: "error",
      });
      return false;
    }
  };

  const createWaveSurfer = async () => {
    if (wavesurfer) {
      wavesurfer.destroy();
    }

    const newWavesurfer = WaveSurfer.create({
      container: waveformRef.current,
      waveColor: theme.palette.text.disabled,
      height: 22,
      barWidth: 1,
      barGap: 4,
      barRadius: 0,
      normalize: true,
      interact: false,
      cursorWidth: 0,
      fillParent: true,
      minPxPerSec: 80,
      autoScroll: true,
      autoCenter: true,
      hideScrollbar: true,
      audioRate: 1,
      responsive: true,
    });

    const newRecord = newWavesurfer.registerPlugin(
      RecordPlugin.create({
        renderRecordedAudio: false,
        scrollingWaveform: true,
        audioBitsPerSecond: 128000,
        scrollingWaveformWindow: 5,
      }),
    );

    newRecord.on("record-progress", (time) => {
      const minutes = Math.floor((time % 3600000) / 60000);
      const seconds = Math.floor((time % 60000) / 1000);
      const formattedTime = [minutes, seconds]
        .map((v) => (v < 10 ? "0" + v : v))
        .join(":");
      setRecordingTime(formattedTime);
    });

    newRecord?.on("record-end", (blob) => {
      setRecordedAudio(blob);
    });

    setWavesurfer(newWavesurfer);
    setRecord(newRecord);

    if (isRecording) {
      const hasPermission = await requestMicrophonePermission();
      if (!hasPermission) {
        return;
      }
      try {
        await newRecord.startRecording();
      } catch (error) {
        logger.error("Error starting recording:", error);
      }
    }
  };

  useEffect(() => {
    createWaveSurfer();
    return () => {
      if (wavesurfer) {
        wavesurfer.destroy();
      }
    };
  }, []);

  const handleRecord = async () => {
    if (isRecording || isPaused) {
      setIsRecording(false);
      setIsPaused(false);
      await record.stopRecording();
      return;
    }

    const hasPermission = await requestMicrophonePermission();
    if (!hasPermission) {
      return;
    }

    try {
      await record.startRecording();
      setIsRecording(true);
    } catch (error) {
      logger.error("Error starting recording:", error);
    }
  };

  if (!isMicrophonePermissionGranted) {
    return (
      <Box
        sx={{
          display: "flex",
          width: "100%",
          justifyContent: "space-between",
          alignItems: "center",
        }}
        onClick={onClose}
      >
        <Typography typography="s1">
          Please allow microphone access to record audio.
        </Typography>
        <IconButton onClick={onClose}>
          <SvgColor
            src="/assets/icons/ic_close.svg"
            sx={{
              width: "16px",
              height: "16px",
            }}
          />
        </IconButton>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          width: "100%",
          overflow: "hidden",
        }}
      >
        <IconButton
          onClick={handleRecord}
          sx={{
            color: "status.danger.fg",
          }}
        >
          <ShowComponent condition={isRecording}>
            <SvgColor
              src="/assets/icons/components/ic_pause.svg"
              sx={{
                width: "20px",
                height: "20px",
              }}
            />
          </ShowComponent>
          <ShowComponent condition={!isRecording}>
            <SvgColor
              src="/assets/icons/navbar/ic_get_started.svg"
              sx={{ width: "20px", height: "20px" }}
            />
          </ShowComponent>
        </IconButton>

        <Box
          ref={waveformRef}
          sx={{
            flex: 1,
            ".wavesurfer-region": {
              backgroundColor: "transparent !important",
            },
            overflow: "hidden",
          }}
        />

        <Box>
          <Typography typography="s2">{recordingTime}</Typography>
        </Box>
      </Box>
    </Box>
  );
};

CustomWaveRecorder.propTypes = {
  onClose: PropTypes.func,
};

export default CustomWaveRecorder;
