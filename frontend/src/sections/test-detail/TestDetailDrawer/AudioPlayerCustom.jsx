import React, { memo, useMemo } from "react";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import CustomAudioPlayer from "src/components/custom-audio/CustomAudioPlayer";
import AudioDownloadButton from "../AudioDownloadButton";
import MultiTrackAudioPlayer, {
  MemoizedBarsIcon,
} from "src/components/multi-track-audio-player/MultiTrackAudioPlayer";
import Iconify from "src/components/iconify";
import LoadingStateComponent from "src/components/CallLogsDetailDrawer/LoadingStateComponent";
import { getLoadingStateWithRespectiveStatus } from "../common";
import { normalizeRecordings } from "src/utils/utils";
import useStereoChannels from "src/hooks/use-stereo-channels";

const isUpdatedWithinTwoMinutes = (timestamp) => {
  if (!timestamp) return false;

  return Date.now() - new Date(timestamp).getTime() < 2 * 60 * 1000;
};

/**
 * Renders MultiTrackAudioPlayer using stereo channel splitting when a stereo
 * URL is available. Falls back to separate mono assistant/customer URLs.
 *
 * Stereo splitting ensures both waveforms share the same duration and timeline,
 * fixing the misaligned waveform issue caused by mono files with different lengths.
 */
export const StereoMultiTrackPlayer = ({
  recordings,
  id,
  height = 70,
  onInstance,
}) => {
  const theme = useTheme();
  // Speaker palette — matches the TranscriptView timeline strip / talk
  // ratio bar so the same role reads the same color across the two UIs.
  const primary =
    theme.palette.mode === "dark"
      ? theme.palette.primary.light
      : theme.palette.primary.main;
  const assistantColor = primary;
  const customerColor = theme.palette.mode === "dark" ? "#FF9933" : "#E9690C";

  const {
    assistantUrl: stereoAssistant,
    customerUrl: stereoCustomer,
    loading: stereoLoading,
    error: stereoError,
  } = useStereoChannels(recordings?.stereo || "");

  // Use stereo-split channels when available, fall back to separate mono files
  const useStereo =
    recordings?.stereo && !stereoError && (stereoLoading || stereoAssistant);

  const assistantUrl = useStereo ? stereoAssistant : recordings?.assistant;
  const customerUrl = useStereo ? stereoCustomer : recordings?.customer;
  const trackUrls = useMemo(
    () => [
      {
        url: customerUrl,
        color: customerColor,
        name: "Customer Audio",
      },
      {
        url: assistantUrl,
        color: assistantColor,
        name: "Assistant Audio",
      },
    ],
    [customerUrl, assistantUrl, customerColor, assistantColor],
  );

  if (useStereo && stereoLoading) {
    return (
      <Box
        sx={{
          minHeight: height * 2 + 20,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 1.5,
        }}
      >
        <MemoizedBarsIcon />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Painting sound waves...
        </Typography>
      </Box>
    );
  }

  return (
    <MultiTrackAudioPlayer
      trackUrls={trackUrls}
      audioUrls={recordings}
      id={id}
      height={height}
      onInstance={onInstance}
    />
  );
};

StereoMultiTrackPlayer.propTypes = {
  recordings: PropTypes.object,
  id: PropTypes.string,
  height: PropTypes.number,
  onInstance: PropTypes.func,
};

const AudioPlayerCustom = ({ data, onInstance }) => {
  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(
      data?.status,
      data?.simulation_call_type,
    );

  const isProjectModule = data?.module === "project";
  if (isCallInProgress) {
    return (
      <Box sx={{ height: 200 }}>
        <LoadingStateComponent message={loadingMessage} />
      </Box>
    );
  }
  if (isProjectModule) {
    if (!data?.recordingAvailable) {
      return (
        <Stack
          justifyContent="center"
          alignItems="center"
          minHeight={200}
          width="100%"
        >
          <Typography typography="s2_1">
            No recording found - <i>{data?.ended_reason}</i>
          </Typography>
        </Stack>
      );
    }

    if (data?.callMetadata?.provider === "retell") {
      return (
        <Stack
          height={50}
          width="100%"
          justifyContent="center"
          alignItems="center"
          direction="row"
        >
          <AudioPlaybackProvider>
            <CustomAudioPlayer
              audioData={{
                url: data?.recording_url,
              }}
              customLoaderComponent={
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 1.5,
                    width: "100%",
                  }}
                >
                  <Iconify
                    icon="svg-spinners:bars-scale"
                    width={20}
                    height={20}
                  />
                  <Typography typography="s1" fontWeight="fontWeightMedium">
                    Painting sound waves...
                  </Typography>
                </Box>
              }
            />
          </AudioPlaybackProvider>
          <AudioDownloadButton
            audioUrls={{
              mono: data.recording_url,
              stereo: "",
              assistant: "",
              customer: "",
            }}
            singleTrack
            size="small"
            sx={{
              minWidth: "32px",
              mr: 1,
              borderRadius: 0.5,
              bgcolor: "background.paper",
            }}
          />
        </Stack>
      );
    }

    // Normalize recordings to flat format for project module
    const normalizedRecordings = normalizeRecordings(data?.recording);
    return (
      <StereoMultiTrackPlayer
        recordings={normalizedRecordings}
        id={data?.id}
        onInstance={onInstance}
      />
    );
  }

  // Normalize recordings structure to flat format: {stereo, combined, assistant, customer}
  const recordings = normalizeRecordings(data?.recordings);
  const hasRecordingData =
    (data?.audio_url ?? data?.audioUrl) ||
    recordings?.assistant ||
    recordings?.customer ||
    recordings?.stereo ||
    recordings?.combined;

  if (
    data?.status === "completed" &&
    !hasRecordingData &&
    isUpdatedWithinTwoMinutes(data?.timestamp)
  ) {
    return (
      <Box sx={{ height: 200 }}>
        <LoadingStateComponent
          status="fetching"
          message={"Fetching the recording"}
        />
      </Box>
    );
  }

  // Show player if we have audioUrl OR recordings data available
  if (hasRecordingData) {
    return (
      <StereoMultiTrackPlayer
        recordings={recordings}
        id={data?.id}
        onInstance={onInstance}
      />
    );
  }

  return (
    <Box
      justifyContent="center"
      alignItems="center"
      display="flex"
      flex={1}
      minHeight={200}
    >
      <Typography typography="s2_1">
        No recording found - <i>{data?.ended_reason}</i>
      </Typography>
    </Box>
  );
};

AudioPlayerCustom.propTypes = {
  data: PropTypes.object,
  onInstance: PropTypes.func,
};

const areRecordingPropsEqual = (prev, next) => {
  // Never skip re-render when a caller uses the onInstance callback — that
  // prop is the audio-sync bridge and must always propagate down.
  if (prev.onInstance !== next.onInstance) return false;
  const p = prev.data;
  const n = next.data;
  return (
    p?.status === n?.status &&
    p?.simulation_call_type === n?.simulation_call_type &&
    p?.module === n?.module &&
    p?.recordingAvailable === n?.recordingAvailable &&
    p?.ended_reason === n?.ended_reason &&
    p?.callMetadata?.provider === n?.callMetadata?.provider &&
    p?.recording_url === n?.recording_url &&
    p?.recording === n?.recording &&
    p?.recordings === n?.recordings &&
    p?.audio_url === n?.audio_url &&
    p?.id === n?.id &&
    p?.timestamp === n?.timestamp
  );
};
//Avoid re-rendering while change in other data other than data of this component
export default memo(AudioPlayerCustom, areRecordingPropsEqual);
