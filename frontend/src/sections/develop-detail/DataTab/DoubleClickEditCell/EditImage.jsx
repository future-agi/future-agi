import { Box, DialogActions, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import ErrorMessage from "./ErrorMessage";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";

const EditImage = ({
  setShowDivider,
  setImageZoomModal,
  params,
  onClose,
  onCellValueChanged,
}) => {
  const [selectedFile, setSelectedFile] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1); // Zoom level
  const [rotation] = useState(0);

  useEffect(() => {
    if (params?.value) {
      setImageUrl(params?.value);
    }
  }, [params?.value]);

  const handleClose = () => {
    onClose();
  };

  const onSubmit = (e) => {
    e.preventDefault();
    try {
      onCellValueChanged({ ...params, newValue: imageUrl });
      // gridApiRef?.current?.api?.refreshServerSide();
      handleClose();
    } catch (err) {
      setError(err?.errors[0]?.message || err?.message || "An error occurred");
    }
  };

  const handleImageChange = (event) => {
    const file = event?.target?.files[0];
    if (file) {
      setSelectedFile(file);
      const reader = new FileReader();
      reader.onloadend = () => {
        const base64String = reader?.result;
        setImageUrl(base64String);
      };
      reader.readAsDataURL(file);
    }
  };

  const handleZoomIn = () => {
    setZoom((prev) => (prev === 1 ? 3 : 1));
    setImageZoomModal(true);
    setShowDivider(false);
  };
  // const handleRotate = () => setRotation((prev) => (prev + 90) % 360);
  const handleDelete = () => {
    setImageUrl("");
  };

  const imageStyles = {
    transform: `scale(${zoom}) rotate(${rotation}deg)`,
    transition: "transform 0.3s ease",
    maxWidth: "100%",
    maxHeight: "100%",
  };

  return (
    <Box
      sx={{
        paddingX: "20px",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        marginTop: "-2px",
      }}
      component="form"
      onSubmit={onSubmit}
    >
      <Box>
        <Box
          sx={{
            position: "relative",
            // color: "#9c27b0",
            width: "421px",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flexDirection: "column",
            height: "245px",
            backgroundColor: "var(--bg-input)",
            border: "1px dashed var(--text-primary)",
          }}
        >
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/bmp,image/tiff"
            style={{
              position: "absolute",
              opacity: 0,
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              cursor: "pointer",
            }}
            onChange={handleImageChange}
          />
          {imageUrl || (selectedFile && imageUrl) ? (
            <Box
              sx={{
                width: "100%",
                height: "100%",
                backgroundColor: "rgba(0, 0, 0,0.5)",
                display: "flex",
                flexDirection: "row",
                justifyContent: "center",
                position: "relative",
                "&:hover .image-action-buttons": {
                  opacity: "1 !important",
                },
              }}
            >
              <img src={imageUrl} alt="Editable" style={imageStyles} />
              <Box
                className="image-action-buttons"
                sx={{
                  opacity: 0,
                  display: "flex",
                  position: "absolute",
                  gap: 2,
                  top: 0,
                  bottom: 0,
                  left: 0,
                  right: 0,
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "rgba(0, 0, 0,0.5)",
                }}
              >
                <IconButton onClick={handleZoomIn}>
                  <Iconify icon="ic:twotone-zoom-in" color="#fff" />
                </IconButton>
                <IconButton>
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/bmp,image/tiff"
                    style={{
                      position: "absolute",
                      opacity: 0,
                      top: 0,
                      left: 0,
                      right: 0,
                      bottom: 0,
                      cursor: "pointer",
                    }}
                    onChange={handleImageChange}
                  />
                  <Iconify icon="fa6-solid:rotate" color="#fff" />
                </IconButton>
                <IconButton onClick={handleDelete}>
                  <Iconify icon="solar:trash-bin-trash-bold" color="#fff" />
                </IconButton>
              </Box>
            </Box>
          ) : (
            <Box
              sx={{
                width: "100%",
                height: "100%",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                flexDirection: "column",
                position: "relative",
                "&:hover .empty-image-action-buttons": {
                  opacity: "1 !important",
                },
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                  flexDirection: "column",
                  gap: 2,
                }}
              >
                <img
                  src="/assets/placeholder.svg"
                  alt="No image placeholder"
                  style={{
                    width: "80px",
                    height: "80px",
                    opacity: 0.6,
                  }}
                />
                <Box sx={{ textAlign: "center" }}>
                  <Typography
                    variant="body2"
                    fontWeight={600}
                    color="text.secondary"
                    sx={{ mb: 0.5 }}
                  >
                    No image added
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Click to upload an image
                  </Typography>
                </Box>
              </Box>
              <Box
                className="empty-image-action-buttons"
                sx={{
                  opacity: 0,
                  display: "flex",
                  position: "absolute",
                  gap: 2,
                  top: 0,
                  bottom: 0,
                  left: 0,
                  right: 0,
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "rgba(0, 0, 0,0.5)",
                }}
              >
                <IconButton>
                  <Iconify icon="fa6-solid:rotate" color="#fff" />
                </IconButton>
              </Box>
            </Box>
          )}
        </Box>

        {error && (
          <ErrorMessage isError={Boolean(error)} errorMessage={error} />
        )}
      </Box>
      <DialogActions
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          marginRight: "-20px",
          marginTop: "-15px",
        }}
      >
        <LoadingButton
          variant="outlined"
          size="small"
          onClick={handleClose}
          sx={{
            width: "90px",
            fontsize: "14px",
            fontWeight: 500,
            marginRight: "-5px",
          }}
        >
          Cancel
        </LoadingButton>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          // fullWidth
          size="small"
          loading={false}
          sx={{ width: "90px", fontSize: "14px", fontWeight: 700 }}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Box>
  );
};

export default EditImage;

EditImage.propTypes = {
  setShowDivider: PropTypes.func,
  setImageZoomModal: PropTypes.func,
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
