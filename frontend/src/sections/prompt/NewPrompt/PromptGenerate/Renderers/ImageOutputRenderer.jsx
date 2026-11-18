import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Modal, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

/**
 * ImageOutputRenderer - Renders image output from prompt runs
 * Displays the image with zoom/fullscreen capability and download option
 */
const ImageOutputRenderer = ({ src, alt = "Generated image" }) => {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [imageError, setImageError] = useState(false);

  const handleDownload = async () => {
    try {
      // For base64 images
      if (src.startsWith("data:image/")) {
        const link = document.createElement("a");
        link.href = src;
        link.download = `generated-image-${Date.now()}.png`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        enqueueSnackbar("Image downloaded successfully", {
          variant: "success",
        });
        return;
      }

      // For URL images - fetch and download
      const response = await fetch(src);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `generated-image-${Date.now()}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      enqueueSnackbar("Image downloaded successfully", { variant: "success" });
    } catch (error) {
      enqueueSnackbar("Failed to download image. Please try again.", {
        variant: "error",
      });
    }
  };

  const handleCopyUrl = async () => {
    try {
      await navigator.clipboard.writeText(src);
      enqueueSnackbar("URL copied to clipboard", { variant: "success" });
    } catch (error) {
      enqueueSnackbar("Failed to copy URL. Please try again.", {
        variant: "error",
      });
    }
  };

  if (!src) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: 200,
          bgcolor: "background.paper",
          borderRadius: 1,
        }}
      >
        <Typography variant="body2" color="text.disabled">
          No image available
        </Typography>
      </Box>
    );
  }

  if (imageError) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: 200,
          bgcolor: "background.paper",
          borderRadius: 1,
          gap: 1,
        }}
      >
        <Iconify
          icon="solar:gallery-linear"
          width={48}
          height={48}
          sx={{ color: "text.disabled" }}
        />
        <Typography variant="body2" color="text.disabled">
          Failed to load image
        </Typography>
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{
            maxWidth: "100%",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            px: 2,
          }}
        >
          {src.substring(0, 100)}...
        </Typography>
      </Box>
    );
  }

  return (
    <>
      <Box
        sx={{
          position: "relative",
          display: "inline-block",
          "&:hover .image-actions": {
            opacity: 1,
          },
        }}
      >
        <Box
          component="img"
          src={src}
          alt={alt}
          onError={() => setImageError(true)}
          onClick={() => setIsFullscreen(true)}
          sx={{
            maxWidth: "100%",
            maxHeight: 400,
            borderRadius: 1,
            cursor: "pointer",
          }}
        />

        {/* Action buttons */}
        <Box
          className="image-actions"
          sx={{
            position: "absolute",
            top: 8,
            right: 8,
            display: "flex",
            gap: 0.5,
            opacity: 0,
            transition: "opacity 0.2s ease-in-out",
            bgcolor: "rgba(0, 0, 0, 0.6)",
            borderRadius: 1,
            p: 0.5,
          }}
        >
          <CustomTooltip show arrow size="small" title="View fullscreen">
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                setIsFullscreen(true);
              }}
              sx={{ color: "common.white" }}
            >
              <Iconify icon="solar:full-screen-linear" width={20} height={20} />
            </IconButton>
          </CustomTooltip>

          <CustomTooltip show arrow size="small" title="Download image">
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                handleDownload();
              }}
              sx={{ color: "common.white" }}
            >
              <Iconify
                icon="solar:download-minimalistic-linear"
                width={20}
                height={20}
              />
            </IconButton>
          </CustomTooltip>

          <CustomTooltip show arrow size="small" title="Copy URL">
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                handleCopyUrl();
              }}
              sx={{ color: "common.white" }}
            >
              <Iconify icon="solar:copy-linear" width={20} height={20} />
            </IconButton>
          </CustomTooltip>
        </Box>
      </Box>

      {/* Fullscreen Modal */}
      <Modal
        open={isFullscreen}
        onClose={() => setIsFullscreen(false)}
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Box
          sx={{
            position: "relative",
            maxWidth: "90vw",
            maxHeight: "90vh",
            outline: "none",
          }}
        >
          <Box
            component="img"
            src={src}
            alt={alt}
            sx={{
              maxWidth: "90vw",
              maxHeight: "90vh",
              objectFit: "contain",
              borderRadius: 1,
            }}
          />

          {/* Close button */}
          <IconButton
            onClick={() => setIsFullscreen(false)}
            sx={{
              position: "absolute",
              top: -40,
              right: 0,
              color: "common.white",
              bgcolor: "rgba(0, 0, 0, 0.6)",
              "&:hover": {
                bgcolor: "rgba(0, 0, 0, 0.8)",
              },
            }}
          >
            <Iconify icon="solar:close-linear" width={24} height={24} />
          </IconButton>

          {/* Action buttons in fullscreen */}
          <Box
            sx={{
              position: "absolute",
              bottom: -48,
              left: "50%",
              transform: "translateX(-50%)",
              display: "flex",
              gap: 1,
              bgcolor: "rgba(0, 0, 0, 0.6)",
              borderRadius: 1,
              p: 1,
            }}
          >
            <CustomTooltip show arrow size="small" title="Download image">
              <IconButton
                size="small"
                onClick={handleDownload}
                sx={{ color: "common.white" }}
              >
                <Iconify
                  icon="solar:download-minimalistic-linear"
                  width={20}
                  height={20}
                />
              </IconButton>
            </CustomTooltip>

            <CustomTooltip show arrow size="small" title="Copy URL">
              <IconButton
                size="small"
                onClick={handleCopyUrl}
                sx={{ color: "common.white" }}
              >
                <Iconify icon="solar:copy-linear" width={20} height={20} />
              </IconButton>
            </CustomTooltip>
          </Box>
        </Box>
      </Modal>
    </>
  );
};

ImageOutputRenderer.propTypes = {
  src: PropTypes.string,
  alt: PropTypes.string,
};

export default ImageOutputRenderer;
