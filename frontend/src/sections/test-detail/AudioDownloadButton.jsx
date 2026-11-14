import React, { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import {
  Popover,
  MenuList,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from "@mui/material";

// Download function that can be reused
const downloadAudioFile = async (url, filename = "audio-recording.wav") => {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to download: ${response.statusText}`);
  }

  const blob = await response.blob();
  const downloadUrl = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(downloadUrl);
  document.body.removeChild(a);
};

const AudioDownloadButton = ({
  audioUrls = {
    mono: "",
    stereo: "",
    assistant: "",
    customer: "",
  },
  filename = "audio-recording.wav",
  singleTrack = false,
  ...buttonProps
}) => {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const downloadMutation = useMutation({
    /**
     *
     * @param {Object} params
     * @returns
     */
    mutationFn: (params) => downloadAudioFile(params.url, params.fileName),
    onSuccess: () => {
      enqueueSnackbar("Audio downloaded successfully", {
        variant: "success",
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to download audio", {
        variant: "error",
      });
    },
  });

  const handleDownload = (audioType, audioUrl) => {
    if (!audioUrl) {
      enqueueSnackbar("Audio file not available", {
        variant: "warning",
      });
      return;
    }
    const fileName = `${audioType}-${filename}`;
    downloadMutation.mutate({ url: audioUrl, fileName });
  };

  return (
    <>
      <LoadingButton
        onClick={() => {
          if (singleTrack) {
            downloadMutation.mutate({
              url: audioUrls?.mono,
              fileName: filename,
            });
          } else {
            setOpen(true);
          }
        }}
        disabled={
          downloadMutation.isPending || Object.keys(audioUrls).length === 0
        }
        startIcon={
          !singleTrack && <Iconify icon="solar:download-minimalistic-linear" />
        }
        {...buttonProps}
        loading={downloadMutation.isPending}
        ref={anchor}
      >
        {singleTrack ? (
          <Iconify icon="solar:download-minimalistic-linear" />
        ) : (
          "Download"
        )}
      </LoadingButton>
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorEl={anchor.current}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        PaperProps={{
          sx: {
            minWidth: 200,
          },
        }}
      >
        <MenuList sx={{ p: 1 }}>
          <MenuItem
            onClick={() => {
              handleDownload("mono", audioUrls.mono);
              setOpen(false);
            }}
            disabled={!audioUrls.mono}
            sx={{
              borderRadius: 1,
            }}
          >
            <ListItemIcon sx={{ mr: 0 }}>
              <Iconify
                icon="solar:volume-cross-linear"
                sx={{
                  fontSize: 20,
                }}
              />
            </ListItemIcon>
            <ListItemText
              primary="Mono Recording"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>

          <MenuItem
            onClick={() => {
              handleDownload("stereo", audioUrls.stereo);
              setOpen(false);
            }}
            disabled={!audioUrls.stereo}
            sx={{
              borderRadius: 1,
            }}
          >
            <ListItemIcon sx={{ mr: 0 }}>
              <Iconify
                icon="solar:volume-loud-linear"
                sx={{
                  fontSize: 20,
                }}
              />
            </ListItemIcon>
            <ListItemText
              primary="Stereo Recording"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>

          <MenuItem
            onClick={() => {
              handleDownload("assistant", audioUrls.assistant);
              setOpen(false);
            }}
            disabled={!audioUrls.assistant}
            sx={{
              borderRadius: 1,
            }}
          >
            <ListItemIcon sx={{ mr: 0 }}>
              <Iconify
                icon="solar:microphone-3-linear"
                sx={{
                  fontSize: 20,
                }}
              />
            </ListItemIcon>
            <ListItemText
              primary="AI Agent recording"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>

          <MenuItem
            onClick={() => {
              handleDownload("customer", audioUrls.customer);
              setOpen(false);
            }}
            disabled={!audioUrls.customer}
            sx={{
              borderRadius: 1,
            }}
          >
            <ListItemIcon sx={{ mr: 0 }}>
              <Iconify
                icon="solar:user-speak-linear"
                sx={{
                  fontSize: 20,
                }}
              />
            </ListItemIcon>
            <ListItemText
              primary=" FAGI Simulator recording"
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>
        </MenuList>
      </Popover>
    </>
  );
};

AudioDownloadButton.propTypes = {
  audioUrls: PropTypes.shape({
    mono: PropTypes.string,
    stereo: PropTypes.string,
    assistant: PropTypes.string,
    customer: PropTypes.string,
  }),
  singleTrack: PropTypes.bool,
  filename: PropTypes.string,
};

export default AudioDownloadButton;
