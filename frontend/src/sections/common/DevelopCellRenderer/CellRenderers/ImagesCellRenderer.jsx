import React, { useState, useMemo } from "react";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import { useDropzone } from "react-dropzone";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";
import SvgColor from "src/components/svg-color";
import DeleteMediaDialog from "src/sections/develop-detail/DataTab/DoubleClickEditCell/ConfirmDelete";
import { useParams } from "react-router";
import logger from "src/utils/logger";
import { enqueueSnackbar } from "notistack";
import GridIcon from "src/components/gridIcon/GridIcon";
import { useMultiImageViewContext } from "src/sections/develop-detail/Common/MultiImageViewer";

const validateFileSize = (file) => {
  const maxSize = 5 * 1024 * 1024;
  if (file.size > maxSize) {
    enqueueSnackbar("File size must be below 5 mb", { variant: "error" });
    return false;
  }
  return true;
};

const ImagesCellRenderer = ({
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  onEditCell,
  params,
  onCellValueChanged,
  editable = false,
}) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isPending] = useState(false);
  const { dataset } = useParams();
  const { setImages } = useMultiImageViewContext();
  const theme = useTheme();

  // Parse value to get array of image URLs
  const imageUrls = useMemo(() => {
    if (!value) return [];
    try {
      const parsed = typeof value === "string" ? JSON.parse(value) : value;
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch {
      // If not valid JSON, treat as single URL
      return [value];
    }
  }, [value]);

  const processFiles = (files) => {
    const validFiles = files.filter(validateFileSize);
    if (validFiles.length === 0) return;

    const newImages = [...imageUrls];
    let processed = 0;

    validFiles.forEach((file) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        newImages.push(reader.result);
        processed++;
        if (processed === validFiles.length) {
          onCellValueChanged?.({
            ...params,
            newValue: JSON.stringify(newImages),
          });
        }
      };
      reader.onerror = () => {
        enqueueSnackbar(`Failed to read file: ${file.name}`, {
          variant: "error",
        });
        processed++;
      };
      reader.readAsDataURL(file);
    });
  };

  const onDrop = (acceptedFiles) => {
    if (acceptedFiles.length > 0) {
      processFiles(acceptedFiles);
    }
  };

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    onDropRejected: () => {
      enqueueSnackbar(
        "Unsupported format. Supported formats: JPEG, PNG, WebP, BMP, TIFF",
        { variant: "error" },
      );
    },
    accept: {
      "image/jpeg": [],
      "image/png": [],
      "image/webp": [],
      "image/bmp": [],
      "image/tiff": [],
    },
    multiple: true,
    noClick: true,
    noKeyboard: true,
  });

  const handleDelete = () => {
    try {
      onCellValueChanged?.({ ...params, newValue: null });
      setShowDeleteDialog(false);
    } catch (err) {
      logger.error("An error occurred while deleting the images:", err);
    }
  };

  const handleImageClick = (index) => {
    setImages(imageUrls, index);
  };

  return (
    <>
      <CustomTooltip
        show={Boolean(valueReason?.length)}
        title={formattedValueReason()}
        enterDelay={500}
        enterNextDelay={500}
        leaveDelay={100}
        arrow
        slotProps={tooltipSlotProp}
      >
        <Box
          sx={{
            display: "flex",
            height: "100%",
            justifyContent: "flex-start",
            padding: "4px 8px",
            position: "relative",
            "&:hover .icon-actions": {
              opacity: 1,
            },
          }}
        >
          {imageUrls.length > 0 ? (
            <Box
              sx={{
                height: "100%",
                width: "100%",
                display: "flex",
                gap: "8px",
                overflowX: "auto",
                "&::-webkit-scrollbar": {
                  height: "4px",
                },
                "&::-webkit-scrollbar-thumb": {
                  backgroundColor: theme.palette.text.disabled,
                  borderRadius: "4px",
                },
              }}
            >
              {imageUrls.map((url, index) => (
                <GridIcon
                  key={index}
                  height="100%"
                  src={url}
                  alt={`Image ${index + 1}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleImageClick(index);
                  }}
                  onMouseEnter={(e) => {
                    window.__imageClick = true;
                    e.stopPropagation();
                  }}
                  onMouseLeave={(e) => {
                    window.__imageClick = false;
                    e.stopPropagation();
                  }}
                  sx={{
                    cursor: "pointer",
                    borderRadius: "8px",
                    minWidth: "80px",
                    maxWidth: "120px",
                    height: "100%",
                    flexShrink: 0,
                  }}
                />
              ))}
            </Box>
          ) : (
            dataset &&
            editable && (
              <Box
                {...getRootProps()}
                sx={{ height: "100%", width: "100%", display: "flex" }}
              >
                <input {...getInputProps()} />
                <Box
                  sx={{
                    height: "100%",
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: "8px",
                    border: "1px dashed",
                    borderColor: theme.palette.divider,
                    minHeight: "60px",
                    backgroundColor: isDragActive
                      ? theme.palette.background.neutral
                      : "transparent",
                    position: "relative",
                    textAlign: "center",
                  }}
                >
                  <Typography
                    variant="s3"
                    color="text.primary"
                    textAlign="center"
                  >
                    <label
                      htmlFor="file-upload"
                      style={{
                        color: "var(--primary-main)",
                        cursor: "pointer",
                      }}
                      onClick={(e) => {
                        e.stopPropagation();
                        window.__imageClick = true;
                        open();
                      }}
                    >
                      Click here
                    </label>{" "}
                    to upload images or <br />
                    drop image files here
                  </Typography>
                </Box>
              </Box>
            )
          )}

          {dataset && imageUrls.length > 0 && editable && (
            <Box
              className="icon-actions"
              sx={{
                position: "absolute",
                top: 4,
                right: 4,
                display: "flex",
                height: "30px",
                opacity: 0,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  window.__imageClick = true;
                  onEditCell?.();
                }}
                aria-label="edit"
              >
                <SvgColor
                  src={`/assets/icons/ic_edit.svg`}
                  sx={{
                    width: 16,
                    height: 16,
                    color: theme.palette.text.primary,
                    pointerEvents: "none",
                  }}
                />
              </IconButton>

              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  window.__imageClick = true;
                  setShowDeleteDialog(true);
                }}
                aria-label="delete"
              >
                <SvgColor
                  src={"/assets/icons/components/ic_delete.svg"}
                  sx={{
                    width: 16,
                    height: 16,
                    color: theme.palette.text.primary,
                    pointerEvents: "none",
                  }}
                />
              </IconButton>
            </Box>
          )}
          <RenderMeta originType={originType} meta={metadata} />
        </Box>
      </CustomTooltip>
      <DeleteMediaDialog
        open={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        onDelete={handleDelete}
        isPending={isPending}
        fileName="these images"
        fileType="images"
      />
    </>
  );
};

ImagesCellRenderer.propTypes = {
  ...commonPropTypes,
};

export default React.memo(ImagesCellRenderer);
