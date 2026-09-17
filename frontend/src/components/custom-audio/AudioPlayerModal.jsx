import React, { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import {
  Box,
  Typography,
  Button,
  LinearProgress,
  Tabs,
  Tab,
  useTheme,
  alpha,
} from "@mui/material";
import PropTypes from "prop-types";
import { grey } from "src/theme/palette";
import { useDropzone } from "react-dropzone";
import UnsupportedFileDialog from "./UnsupportedFileDialog";
import CancelUploadDialog from "./CancelUploadDialog";
import { useForm } from "react-hook-form";
import { LoadingButton } from "@mui/lab";
import { red } from "src/theme/palette";
import { enqueueSnackbar } from "notistack";
import { formatFileSize } from "src/utils/utils";
import { getAudioErrorMessage } from "./audioHelper";
import SvgColor from "../svg-color";
import DeleteMediaDialog from "src/sections/develop-detail/DataTab/DoubleClickEditCell/ConfirmDelete";
import { ShowComponent } from "../show";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";
import { fileIconByMimeType } from "../../utils/constants";
import TestAudioPlayer from "./TestAudioPlayer";
import { useDevelopDetailContext } from "../../sections/develop-detail/Context/DevelopDetailContext";

function CustomTabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && <Box>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  index: PropTypes.number.isRequired,
  value: PropTypes.number.isRequired,
};

const modalStyle = {
  width: 577,
  p: 2,
  pt: 1,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const extensionToType = {
  mp3: "audio/mp3",
  wav: "audio/wav",
  mpeg: "audio/mpeg",
};

const AudioWaveformModal = ({ onClose, params, onCellValueChanged }) => {
  const [audioData, setAudioData] = useState(null);
  const fileInputRef = useRef(null);
  const theme = useTheme();
  const [, setAudioURL] = useState("");
  const [unsupportedFile, setUnsupportedFile] = useState(false);
  const [isCancelDialogOpen, setCancelDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [tabIndex, setTabIndex] = useState(0);
  const [error, setError] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [, setUserCanceled] = useState(false);
  const [fileType, setFileType] = useState("");
  const [hasChanged, setHasChanged] = useState(false);
  const { control, watch, reset, handleSubmit } = useForm({
    defaultValues: { audioUrl: "" },
  });
  const [showPreview, setShowPreview] = useState(false);
  const [previewFile, setPreviewFile] = useState(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const { refreshGrid } = useDevelopDetailContext();

  const iconStyles = {
    width: 16,
    height: 16,
    color: theme.palette.text.primary,
  };

  useEffect(() => {
    if (params?.value) {
      setFileType("audio/mp3");
      setAudioData({
        url: params.value,
        fileName: "audio",
        fileType: "audio/mp3",
        size: "-",
      });
    }
  }, [params?.value]);

  const audioUrl = watch("audioUrl");

  const onDrop = useCallback((acceptedFiles, rejectedFiles) => {
    if (rejectedFiles.length > 0) {
      setUnsupportedFile(true);
      return;
    }
    setError(false);
    setErrorMessage("");
    setUserCanceled(false);
    if (acceptedFiles.length > 0) {
      const file = acceptedFiles[0];
      setHasChanged(true);
      const blobUrl = URL.createObjectURL(file);
      setAudioURL(blobUrl);
      setSelectedFile(file);

      const fileExtension = file.name.split(".").pop().toLowerCase();

      const mappedFileType = extensionToType[fileExtension] || "";

      setAudioData({
        url: blobUrl,
        fileName: file.name,
        fileType: mappedFileType,
        size: formatFileSize(file.size),
      });
      setSelectedFile(file);
      setFileType(mappedFileType);
    }
  }, []);

  const onSubmitFileUpload = () => {
    if (showPreview && previewFile) {
      setLoading(true);
      setAudioData({
        url: previewFile.url,
        fileName: previewFile.fileName,
        fileType: previewFile.fileType,
        size: previewFile.size,
      });
      setSelectedFile(previewFile.file);
      setShowPreview(false);
      return;
    }
    if (error || !audioData) {
      setError(true);
      setErrorMessage(
        "Please ensure the audio file loads correctly before uploading.",
      );
      setLoading(false);
      return;
    }

    try {
      onCellValueChanged({
        ...params,
        newValue: selectedFile,
        onSuccess: () => {
          refreshGrid();
          enqueueSnackbar(`${selectedFile.name} file has been updated`, {
            variant: "success",
          });
        },
      });
      onClose();
    } catch (err) {
      setError(
        err?.errors?.[0]?.message || err?.message || "An error occurred",
      );
    }
  };

  const onFetchAudio = (data) => {
    setLoading(true);
    setError(false);
    setAudioURL(data.audioUrl);
    if (data.audioUrl !== params?.value) {
      setHasChanged(true);
    }
    const fileExtension = data.audioUrl.split(".").pop().toLowerCase();
    const mappedFileType = extensionToType[fileExtension] || "";

    setTimeout(() => {
      (async () => {
        try {
          if (audioData) {
            onClose();
            await onCellValueChanged({ ...params, newValue: data.audioUrl });
          }

          const newAudioData = {
            url: data.audioUrl,
            fileName: "Web Link Audio",
            fileType: mappedFileType,
            size: null,
          };

          setFileType(mappedFileType);
          setAudioData(newAudioData);
        } catch (err) {
          setError(
            err?.errors?.[0]?.message || err?.message || "An error occurred",
          );
        }
      })();
    }, 50);
  };

  const handleRemoveAudio = () => {
    setAudioData(null);
    setPreviewFile(null);
    setLoading(false);
  };

  const handleCancelUpload = () => {
    setUserCanceled(true);
    setCancelDialogOpen(true);
  };

  const confirmCancelUpload = () => {
    setError(false);
    setErrorMessage("");
    setCancelDialogOpen(false);
    setAudioData(null);
    setLoading(false);
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: {
      "audio/mp3": [".mp3"],
      "audio/wav": [".wav"],
      "audio/mpeg": [".mpeg"],
    },
    onDrop,
    multiple: false,
  });

  useEffect(() => {
    setError(false);
    setLoading(false);
    setUserCanceled(false);
    reset();
  }, [reset, tabIndex]);

  const handleCloseModal = () => {
    setAudioData(null);
    setUserCanceled(false);
    setError(false);
    setLoading(false);
    setTabIndex(0);
    reset();
    onClose();
  };

  const handleDelete = () => {
    setShowDeleteDialog(false);
    handleCloseModal();
    onCellValueChanged({ ...params, newValue: null });
  };

  const isDisabled =
    (!hasChanged && audioData) ||
    (tabIndex === 0 && !previewFile && !audioData) ||
    (tabIndex === 1 && !audioUrl) ||
    loading;

  return (
    <>
      <Box>
        <Box sx={modalStyle}>
          <ShowComponent condition={!audioData}>
            <Box
              sx={{
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Tabs
                value={tabIndex}
                onChange={(e, newIndex) => {
                  setTabIndex(newIndex);
                }}
                textColor="primary"
                indicatorColor="primary"
                sx={{
                  marginLeft: "2%",
                  "& .Mui-selected": {
                    color: "primary.main",
                  },
                  "& .MuiTabs-indicator": {
                    backgroundColor: "primary.main",
                  },
                }}
              >
                <Tab label="Upload" />
                <Tab label="Using Weblink" />
              </Tabs>
            </Box>
          </ShowComponent>

          <ShowComponent condition={!audioData}>
            <>
              <CustomTabPanel value={tabIndex} index={0}>
                <ShowComponent condition={!showPreview}>
                  <Box
                    {...getRootProps()}
                    sx={{
                      border: (theme) =>
                        `1px dashed ${alpha(theme.palette.primary.main, 0.3)}`,
                      transition: (theme) =>
                        theme.transitions.create([
                          "opacity",
                          "padding",
                          "border-color",
                          "background-color",
                        ]),
                      "&:hover": {
                        bgcolor: (theme) =>
                          alpha(theme.palette.primary.main, 0.08),
                        borderColor: (theme) =>
                          alpha(theme.palette.primary.main, 0.5),
                      },
                      borderRadius: (theme) => theme.spacing(1),
                      padding: (theme) => theme.spacing(3),
                      textAlign: "center",
                      height: "210px",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "center",
                      alignItems: "center",
                      cursor: "pointer",
                      paddingX: (theme) => theme.spacing(5),
                      outline: "none",
                      overflow: "hidden",
                      position: "relative",
                      backgroundColor: (theme) => theme.palette.action.hover,
                      ...(isDragActive && {
                        bgcolor: (theme) =>
                          alpha(theme.palette.primary.main, 0.12),
                        borderColor: (theme) => theme.palette.primary.main,
                      }),
                    }}
                  >
                    <input
                      {...getInputProps({ refKey: "ref" })}
                      ref={fileInputRef}
                    />
                    <Icon
                      icon="lucide:download"
                      width="22"
                      color="var(--primary-main)"
                    />
                    <Box
                      sx={{
                        display: "flex",
                        gap: (theme) => theme.spacing(1),
                        flexDirection: "column",
                        alignItems: "center",
                      }}
                    >
                      <Typography
                        variant="m3"
                        fontWeight="fontWeightSemiBold"
                        sx={{ mt: (theme) => theme.spacing(1) }}
                      >
                        {isDragActive
                          ? "Drop the audio file here..."
                          : "Choose a file or drag & drop it here"}
                      </Typography>
                      <Typography sx={{ fontSize: "12px", color: grey[500] }}>
                        MP3, WAV, MPEG format
                      </Typography>
                      <Button
                        variant="outlined"
                        size="small"
                        sx={{
                          paddingY: (theme) => theme.spacing(0.75),
                          paddingX: (theme) => theme.spacing(3),
                          borderRadius: (theme) => theme.spacing(1),
                          background: (theme) => theme.palette.divider,
                          color: "text.primary",
                          borderColor: "text.disabled",
                        }}
                        onClick={() => fileInputRef?.current?.click()}
                      >
                        Browse files
                      </Button>
                    </Box>
                  </Box>
                </ShowComponent>

                <ShowComponent condition={showPreview}>
                  <Box
                    display="flex"
                    justifyContent="center"
                    flexDirection="column"
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: (theme) => theme.spacing(1),
                      position: "relative",
                      padding: (theme) => theme.spacing(2),
                      mt: (theme) => theme.spacing(2),
                    }}
                  >
                    <Box
                      display="flex"
                      alignItems="center"
                      justifyContent="space-between"
                      marginLeft="5px"
                    >
                      <Box display="flex" alignItems="center" gap={1}>
                        {fileIconByMimeType[previewFile?.fileType] && (
                          <img
                            src={fileIconByMimeType[previewFile.fileType]}
                            alt="File Icon"
                          />
                        )}
                        <Box>
                          <Typography
                            fontWeight="fontWeightMedium"
                            variant="s1"
                          >
                            {previewFile?.fileName}
                          </Typography>
                          {previewFile?.size && (
                            <Typography
                              variant="s2"
                              fontWeight="fontWeightRegular"
                              color={theme.palette.text.disabled}
                            >
                              {previewFile.size} of {previewFile.size}
                            </Typography>
                          )}
                        </Box>
                      </Box>
                      <LoadingButton
                        onClick={() => {
                          setShowPreview(false);
                          setPreviewFile(null);
                        }}
                        size="small"
                        startIcon={
                          <SvgColor
                            src={"/assets/icons/components/ic_delete.svg"}
                            sx={iconStyles}
                          />
                        }
                        sx={{
                          position: "absolute",
                          right: 20,
                          top: 5,
                          fontWeight: 400,
                          fontSize: "12px",
                        }}
                      >
                        Remove
                      </LoadingButton>
                    </Box>
                  </Box>
                </ShowComponent>
              </CustomTabPanel>

              <CustomTabPanel value={tabIndex} index={1}>
                <form onSubmit={handleSubmit(onFetchAudio)}>
                  <FormTextFieldV2
                    control={control}
                    placeholder="Enter audio url"
                    fieldName="audioUrl"
                    label="Link"
                    fullWidth
                    size="small"
                    error={error}
                    sx={{
                      "& .MuiOutlinedInput-root": {
                        ...(error && {
                          boxShadow: `inset 0 0 0 1px ${red[500]}`,
                          borderRadius: "4px",
                        }),
                      },
                    }}
                  />
                </form>
              </CustomTabPanel>
            </>
          </ShowComponent>

          <ShowComponent condition={!!audioData}>
            <Box
              display="flex"
              justifyContent="center"
              flexDirection="column"
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                position: "relative",
                padding: 2,
              }}
            >
              <Box
                display="flex"
                alignItems="center"
                justifyContent="space-between"
                marginLeft="5px"
              >
                <Box display="flex" alignItems="center" gap={1}>
                  {fileIconByMimeType[fileType] && (
                    <img
                      src={fileIconByMimeType[fileType]}
                      alt="File Icon"
                      width={22}
                    />
                  )}
                  <Box>
                    <Typography
                      sx={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        maxWidth: "400px",
                      }}
                      fontWeight={500}
                      fontSize="14px"
                    >
                      {audioData?.fileName}
                    </Typography>
                    {audioData?.size && (
                      <Typography fontSize="12px" color="text.disabled">
                        {audioData.size} of {audioData.size}
                      </Typography>
                    )}
                  </Box>
                </Box>
                <LoadingButton
                  onClick={
                    loading
                      ? handleCancelUpload
                      : () => setShowDeleteDialog(true)
                  }
                  size="small"
                  startIcon={
                    loading ? (
                      <Icon
                        icon="material-symbols:close"
                        color="black"
                        width={16}
                      />
                    ) : (
                      <SvgColor
                        src={"/assets/icons/components/ic_delete.svg"}
                        sx={iconStyles}
                      />
                    )
                  }
                  sx={{
                    position: "absolute",
                    right: 10,
                    top: 5,
                    fontWeight: 400,
                    fontSize: "12px",
                  }}
                >
                  {loading ? "Cancel" : "Delete"}
                </LoadingButton>
                <ShowComponent condition={!loading}>
                  <LoadingButton
                    onClick={handleRemoveAudio}
                    size="small"
                    startIcon={
                      <SvgColor
                        src={"/assets/icons/components/ic_replace.svg"}
                        sx={iconStyles}
                      />
                    }
                    sx={{
                      position: "absolute",
                      right: 90,
                      top: 5,
                      fontWeight: 400,
                      fontSize: "12px",
                    }}
                  >
                    Replace
                  </LoadingButton>
                </ShowComponent>
              </Box>
              <Box sx={{ width: "100%", mt: 2, position: "relative" }}>
                <ShowComponent condition={loading}>
                  <LinearProgress
                    sx={{ mt: 1, position: "absolute", width: "100%" }}
                  />
                </ShowComponent>
                <ShowComponent condition={!!audioData}>
                  <Box sx={{ visibility: loading ? "hidden" : "visible" }}>
                    <TestAudioPlayer
                      audioData={audioData}
                      onAudioReady={() => {
                        setLoading(false);
                        setUserCanceled(false);
                      }}
                      onAudioError={(err) => {
                        const { message, isAbort } = getAudioErrorMessage(err);

                        if (isAbort) {
                          setLoading(false);
                          return;
                        }

                        setError(true);
                        setErrorMessage(message);
                        setAudioData(null);
                        setAudioURL("");
                        setLoading(false);
                      }}
                      updateWaveSurferInstance={() => {}}
                    />
                  </Box>
                </ShowComponent>
                <ShowComponent condition={!audioData}>
                  <Typography variant="body1">
                    No audio file selected.
                  </Typography>
                </ShowComponent>
              </Box>
            </Box>
          </ShowComponent>

          <ShowComponent condition={error}>
            <Typography sx={{ fontSize: "12px", mt: 1 }}>
              {errorMessage}{" "}
              <Box
                component="span"
                onClick={() => {
                  setError(false);
                  setAudioData(null);
                  setLoading(true);
                  if (tabIndex === 1) {
                    handleSubmit(onFetchAudio)();
                  } else {
                    setLoading(false);
                    fileInputRef.current?.click();
                  }
                }}
                sx={{
                  fontWeight: "bold",
                  color: "red.500",
                  textDecoration: "underline",
                  cursor: "pointer",
                }}
              >
                Try again
              </Box>
            </Typography>
          </ShowComponent>

          <Box display="flex" justifyContent="flex-end">
            <LoadingButton
              variant="contained"
              disabled={isDisabled}
              loading={loading}
              onClick={() => {
                if (tabIndex === 1) {
                  handleSubmit(onFetchAudio)();
                } else if (showPreview) {
                  onSubmitFileUpload();
                } else {
                  onSubmitFileUpload();
                }
              }}
              sx={{
                backgroundColor: theme.palette.primary.main,
                "&:hover": { backgroundColor: theme.palette.primary.main },
                borderRadius: "10px",
                fontSize: "14px",
                fontWeight: 600,
                width: "205px",
              }}
            >
              {!loading && (
                <SvgColor
                  src={`/assets/icons/components/ic_save.svg`}
                  sx={{
                    width: 20,
                    height: 20,
                    mr: 1,
                    color: isDisabled
                      ? theme.palette.divider
                      : theme.palette.divider,
                  }}
                />
              )}
              Save
            </LoadingButton>
          </Box>
        </Box>
      </Box>

      <UnsupportedFileDialog
        open={unsupportedFile}
        onClose={() => setUnsupportedFile(false)}
        onUpload={() => setUnsupportedFile(false)}
      />
      <CancelUploadDialog
        open={isCancelDialogOpen}
        onClose={() => setCancelDialogOpen(false)}
        onConfirmCancel={confirmCancelUpload}
      />
      <DeleteMediaDialog
        open={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        onDelete={handleDelete}
        isPending={loading}
        fileName={audioData?.fileName || "this audio"}
        fileType="audio"
      />
    </>
  );
};

AudioWaveformModal.propTypes = {
  onClose: PropTypes.func,
  params: PropTypes.object,
  onCellValueChanged: PropTypes.func,
};

export default AudioWaveformModal;
