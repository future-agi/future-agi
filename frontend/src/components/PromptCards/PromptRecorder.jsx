import { Box, CircularProgress, IconButton } from "@mui/material";
import React, { useContext } from "react";
import {
  CustomWaveRecorder,
  CustomWaveRecorderContext,
  CustomWaveRecorderProvider,
} from "../CustomWaveRecorder";
import { ShowComponent } from "../show";
import SvgColor from "../svg-color";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "src/components/snackbar";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const PromptRecorderChild = ({ onClose, handleEmbedMedia }) => {
  const { isRecording, recordedAudio } = useContext(CustomWaveRecorderContext);

  const { mutate: uploadFile, isPending } = useMutation({
    mutationFn: (audioBlob) => {
      const formData = new FormData();
      formData.append("files", audioBlob);

      formData.append("type", "audio");
      return axios.post(endpoints.misc.uploadFile, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });
    },
    onSuccess: (data) => {
      const uploadedUrl = data?.data?.result || [];
      const mappedMediaData = uploadedUrl.reduce((acc, data) => {
        if (!data.url) return acc;

        acc.push({
          url: data.url,
          audio_name: data?.fileName ?? "Recorded Audio",
          audio_size: recordedAudio?.size,
          audio_type: "audio/mp3",
        });
        return acc;
      }, []);
      onClose();
      handleEmbedMedia("audio", mappedMediaData);
    },
  });

  const handleSave = () => {
    if (!recordedAudio) {
      enqueueSnackbar("Some error recording audio, please try again", {
        variant: "error",
      });
      return;
    }

    uploadFile(recordedAudio);
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        width: "100%",
        p: "12px",
        overflow: "hidden",
        border: "1px solid",
        borderRadius: 1,
        borderColor: "divider",
        backgroundColor: "background.paper",
      }}
    >
      <CustomWaveRecorder onClose={onClose} />
      <ShowComponent condition={isPending}>
        <Box>
          <CircularProgress size={12} color="inherit" />
        </Box>
      </ShowComponent>
      <ShowComponent condition={!isRecording && !isPending}>
        <IconButton onClick={handleSave}>
          <SvgColor
            src="/assets/icons/ic_tick.svg"
            sx={{
              width: "16px",
              height: "16px",
            }}
          />
        </IconButton>
        <IconButton onClick={onClose} disabled={isPending}>
          <SvgColor
            src="/assets/icons/ic_close.svg"
            sx={{
              width: "16px",
              height: "16px",
            }}
          />
        </IconButton>
      </ShowComponent>
    </Box>
  );
};

PromptRecorderChild.propTypes = {
  onClose: PropTypes.func,
  handleEmbedMedia: PropTypes.func,
};

const PromptRecorder = ({ onClose, handleEmbedMedia }) => {
  return (
    <CustomWaveRecorderProvider defaultRecording={true}>
      <PromptRecorderChild
        onClose={onClose}
        handleEmbedMedia={handleEmbedMedia}
      />
    </CustomWaveRecorderProvider>
  );
};

PromptRecorder.propTypes = {
  onClose: PropTypes.func,
  handleEmbedMedia: PropTypes.func,
};

export default PromptRecorder;
