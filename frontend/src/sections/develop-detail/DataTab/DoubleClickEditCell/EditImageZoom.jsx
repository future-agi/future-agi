import {
  Box,
  CircularProgress,
  DialogActions,
  Typography,
  IconButton,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import ErrorMessage from "./ErrorMessage";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import DeleteMediaDialog from "./ConfirmDelete";

const EditImageZoom = ({
  setImageZoomModal,
  params,
  onClose,
  onCellValueChanged,
}) => {
  const fileInputRef = useRef(null);
  const [selectedFile, setSelectedFile] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [error, setError] = useState("");
  const [zoom] = useState(1); // Zoom level
  const [rotation] = useState(0);
  const [imgDimensions, setImgDimensions] = useState({ width: 0, height: 0 });
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);

  useEffect(() => {
    if (params?.value) {
      setIsLoading(true);
      setImageUrl(params.value);
      setImgDimensions({ width: 0, height: 0 });

      const img = new window.Image();
      img.onload = () => {
        setImgDimensions({
          width: img.naturalWidth,
          height: img.naturalHeight,
        });
        setIsLoading(false);
      };
      img.onerror = () => {
        setIsLoading(false);
      };
      img.src = params.value;
    } else {
      // No image value, so we're not loading
      setIsLoading(false);
    }
  }, [params?.value]);

  const handleClose = () => {
    setImageZoomModal(false);
    onClose();
  };

  const handleButtonClick = () => {
    // Trigger the file input click programmatically
    fileInputRef?.current?.click();
  };

  const onSubmit = (e) => {
    e.preventDefault();
    try {
      onCellValueChanged({
        ...params,
        newValue: imageUrl,
        onSuccess: () => {
          enqueueSnackbar("Image has been updated", { variant: "success" });
        },
      });
      handleClose();
    } catch (err) {
      setError(
        err?.errors[0]?.message || "An error occurred while saving the image",
      );
    }
  };

  const handleConfirmDelete = () => {
    setImageUrl("");
    setIsDeleteDialogOpen(false);
  };

  const handleImageChange = (event) => {
    const file = event?.target?.files?.[0];
    if (file) {
      setIsLoading(true);
      setImgDimensions({ width: 0, height: 0 });
      setSelectedFile(file);

      const reader = new FileReader();
      reader.onloadend = () => {
        const base64String = reader.result;

        const img = new window.Image();
        img.onload = () => {
          setImgDimensions({
            width: img.naturalWidth,
            height: img.naturalHeight,
          });
          setImageUrl(base64String);
          setIsLoading(false);
        };
        img.onerror = () => {
          setIsLoading(false);
        };
        img.src = base64String;
      };

      reader.readAsDataURL(file);
    }
  };

  const imageStyles = {
    transform: `scale(${zoom}) rotate(${rotation}deg)`,
    transition: "transform 0.3s ease",
    maxWidth: "100%",
    maxHeight: "100%",
    objectFit: "contain",
  };

  const containerStyles = {
    position: "relative",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    flexDirection: "column",
    backgroundColor: "var(--bg-input)",
    width: imgDimensions.width || "500px",
    height: imgDimensions.height || "500px",
    maxHeight: "500px",
    maxWidth: "500px",
    overflow: "hidden",
  };

  const imageButtons = [
    {
      icon: "ic_replace",
      title: "Replace",
      action: handleButtonClick,
    },
    {
      icon: "ic_delete",
      title: "Delete",
      action: () => setIsDeleteDialogOpen(true),
    },
  ];

  const buttonStyles = {
    color: "text.primary",
    fontSize: "14px",
    fontWeight: 400,
    lineHeight: "22px",
  };

  const iconStyles = {
    width: 24,
    height: 24,
    color: "text.primary",
  };

  return (
    <>
      <Box
        sx={{
          padding: "16px",
          paddingTop: "0px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
        }}
        component="form"
        onSubmit={onSubmit}
      >
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            padding: "12px",
            backgroundColor: "background.paper",
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "center",
            }}
          >
            <Box sx={containerStyles}>
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
              <ShowComponent condition={isLoading}>
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    width: "100%",
                    height: "100%",
                  }}
                >
                  <CircularProgress />
                </Box>
              </ShowComponent>
              <ShowComponent condition={!isLoading && Boolean(imageUrl)}>
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
                </Box>
              </ShowComponent>
              <ShowComponent condition={!isLoading && !imageUrl}>
                <Box
                  sx={{
                    width: "100%",
                    height: "100%",
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
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
                        width: "100px",
                        height: "100px",
                        opacity: 0.6,
                      }}
                    />
                    <Box sx={{ textAlign: "center" }}>
                      <Typography
                        variant="body1"
                        fontWeight={600}
                        color="text.secondary"
                        sx={{ mb: 0.5 }}
                      >
                        No image added
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
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
                    <IconButton onClick={handleButtonClick}>
                      <Iconify icon="fa6-solid:rotate" color="#fff" />
                    </IconButton>
                  </Box>
                </Box>
              </ShowComponent>
            </Box>

            {error && (
              <ErrorMessage isError={Boolean(error)} errorMessage={error} />
            )}
          </Box>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 2,
              mt: 1,
            }}
          >
            <Box sx={{ minWidth: "200px" }}>
              {selectedFile?.name && selectedFile?.size ? (
                <>
                  <Typography
                    fontWeight={500}
                    sx={{
                      maxWidth: "200px",
                      fontSize: "14px",
                      lineHeight: "22px",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      color: "text.primary",
                    }}
                  >
                    {selectedFile?.name}
                  </Typography>
                  <Typography sx={{ color: "text.disabled", fontSize: "12px" }}>
                    {`${(selectedFile?.size / 1024).toFixed(1)} KB of ${(selectedFile?.size / 1024).toFixed(1)} KB`}
                  </Typography>
                </>
              ) : (
                <Box sx={{ height: "38px" }} />
              )}
            </Box>

            <Box sx={{ display: "flex", gap: 1 }}>
              {imageButtons.map((button, index) => (
                <LoadingButton
                  key={index}
                  size="small"
                  startIcon={
                    <SvgColor
                      src={`/assets/icons/components/${button.icon}.svg`}
                      sx={iconStyles}
                    />
                  }
                  onClick={button.action}
                  sx={buttonStyles}
                >
                  {button.title}
                  {button.title === "Replace" && (
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp,image/bmp,image/tiff"
                      ref={fileInputRef}
                      style={{ display: "none" }}
                      onChange={handleImageChange}
                    />
                  )}
                </LoadingButton>
              ))}
            </Box>
          </Box>
        </Box>
        <DialogActions sx={{ justifyContent: "flex-end", padding: 0 }}>
          <LoadingButton
            variant="contained"
            type="submit"
            size="medium"
            sx={{
              backgroundColor: "primary.main",
              "&:hover": { backgroundColor: "primary.main" },
              borderRadius: "10px",
              fontSize: "14px",
              fontWeight: 600,
              width: "205px",
            }}
          >
            <SvgColor
              src={`/assets/icons/components/ic_save.svg`}
              sx={{
                width: 20,
                height: 20,
                mr: 1,
                color: "divider",
              }}
            />
            Save
          </LoadingButton>
        </DialogActions>
      </Box>
      <DeleteMediaDialog
        open={isDeleteDialogOpen}
        onClose={() => setIsDeleteDialogOpen(false)}
        onDelete={handleConfirmDelete}
        isPending={false}
        fileName={selectedFile?.name}
        fileType="image"
      />
    </>
  );
};

export default EditImageZoom;

EditImageZoom.propTypes = {
  gridApiRef: PropTypes.object,
  setImageZoomModal: PropTypes.func,
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
