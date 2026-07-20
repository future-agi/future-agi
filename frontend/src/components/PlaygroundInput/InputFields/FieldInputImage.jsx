import { Box, CircularProgress, IconButton, Typography } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useDropzone } from "react-dropzone";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import AudioEmbed from "src/components/PromptCards/EmbedComponents/AudioEmbed";
import ImageEmbed from "src/components/PromptCards/EmbedComponents/ImageEmbed";
import ViewReplaceImage from "src/components/PromptCards/ViewReplaceImage";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { IMAGE_ACCEPTED_TYPES, MAX_IMAGE_SIZE_BYTES } from "src/sections/agent-playground/utils/constants";

const FieldInputImage = ({ data, onChange, type, internalState }) => {
  const [selectedImage, setSelectedImage] = useState(null);
  const { getRootProps, getInputProps, isDragActive, fileRejections } =
    useDropzone({
      onDrop: (acceptedFiles) => {
        if (acceptedFiles.length > 0) {
          uploadFile(acceptedFiles[0]);
        }
      },
      accept:
        type === "image"
          ? IMAGE_ACCEPTED_TYPES
          : { "audio/*": [".mp3"] }, // Restrict to common audio formats
      maxSize: type === "audio" ? 20 * 1024 * 1024 : MAX_IMAGE_SIZE_BYTES,
    });

  const {
    mutate: uploadFile,
    isPending,
    error,
  } = useMutation({
    mutationFn: (file) => {
      const formData = new FormData();
      formData.append("files", file);
      formData.append("type", type);

      return axios.post(endpoints.misc.uploadFile, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: (data, variable) => {
      const uploadedUrl = data?.data?.result || [];
      const mappedMediaData = uploadedUrl.reduce((acc, item) => {
        if (item.url) {
          acc.push({ url: item.url, type, value: "", file: variable });
        }
        return acc;
      }, []);
      if (onChange && mappedMediaData.length > 0) {
        onChange(mappedMediaData[0]); // Pass only the URL to match PropTypes.string
      }
    },
  });

  if (isPending) {
    return <CircularProgress size={24} />;
  }

  const handleRemoveImage = () => {
    if (onChange) {
      onChange({ url: "", type, value: "", file: null }); // Reset to empty string
    }
  };

  const handleReplaceImage = (_imgid, newImageData) => {
    if (onChange) {
      onChange({
        url: newImageData?.url || "",
        type,
        value: "",
        file: {
          name: newImageData?.img_name || "",
          size: newImageData?.img_size || null,
        },
      });
    }
  };

  return (
    <Box sx={{}}>
      <input {...getInputProps()} />
      <ShowComponent condition={!data}>
        <Box
          {...getRootProps()}
          sx={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
            gap: 0.5,
            cursor: "pointer",
            border: "1px dashed",
            borderColor: isDragActive ? "primary.main" : "divider",
            backgroundColor: isDragActive
              ? "primary.lighter"
              : "background.paper",
            borderRadius: "8px",
            transition: "all 0.2s ease",
            p: 1.5,
          }}
        >
          <IconButton sx={{ borderRadius: "4px" }} disabled={isPending}>
            <SvgColor src="/icons/datasets/upload_file.svg" />
          </IconButton>
          <Box textAlign="center">
            <Typography
              variant="s3"
              fontWeight="fontWeightRegular"
              color="primary.main"
              component="span"
            >
              Click here{" "}
            </Typography>
            <Typography
              variant="s3"
              fontWeight="fontWeightRegular"
              color="text.primary"
              component="span"
            >
              to upload {type} file or drop media here
            </Typography>
          </Box>
          {error && (
            <Typography variant="caption" color="error.main">
              Upload failed: {error.response?.data?.message || error.message}
            </Typography>
          )}
          {fileRejections.length > 0 && (
            <Typography variant="caption" color="error.main">
              File rejected: {fileRejections[0].errors[0].message}
            </Typography>
          )}
        </Box>
      </ShowComponent>
      <ShowComponent condition={Boolean(data) && type === "image"}>
        <ImageEmbed
          url={data}
          name={internalState?.file?.name || "Image"}
          size={internalState?.file?.size || null}
          onDelete={handleRemoveImage}
          onMagnify={() =>
            setSelectedImage({
              url: data,
              name: internalState?.file?.name || "Image",
              size: null,
              id: null,
            })
          }
          onReplace={() =>
            setSelectedImage({
              url: data,
              name: internalState?.file?.name || "Image",
              size: null,
              id: null,
              replace: true,
            })
          }
        />
      </ShowComponent>
      <ShowComponent condition={Boolean(data) && type === "audio"}>
        <AudioPlaybackProvider>
          <AudioEmbed
            url={data}
            name={internalState?.file?.name || "Audio"}
            onDelete={handleRemoveImage}
            mimeType={"audio/mp3"}
          />
        </AudioPlaybackProvider>
      </ShowComponent>
      <ViewReplaceImage
        open={Boolean(selectedImage)}
        onClose={() => setSelectedImage(null)}
        selectedImage={selectedImage}
        onImageDelete={handleRemoveImage}
        onImageReplace={handleReplaceImage}
      />
    </Box>
  );
};

export default FieldInputImage;

FieldInputImage.propTypes = {
  data: PropTypes.string,
  onChange: PropTypes.func,
  type: PropTypes.oneOf(["image", "audio"]).isRequired,
  internalState: PropTypes.object,
};
