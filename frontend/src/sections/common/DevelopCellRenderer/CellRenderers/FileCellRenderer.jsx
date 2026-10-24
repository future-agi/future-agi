import React, { useEffect, useState } from "react";
import { Box, IconButton, Stack, Typography, useTheme } from "@mui/material";
import { useDropzone } from "react-dropzone";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";
import DeleteMediaDialog from "src/sections/develop-detail/DataTab/DoubleClickEditCell/ConfirmDelete";
import { useParams } from "react-router";
import logger from "src/utils/logger";
import { enqueueSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import { getFileType, getFileTypeFromMime } from "./common";
import ReplaceMediaDialog from "../../../develop-detail/DataTab/DoubleClickEditCell/ReplaceMediaDialog";
import { getFileIcon } from "../../../knowledge-base/sheet-view/icons";

import { usePdfPreviewStoreShallow } from "src/utils/CommonStores/pdfPreviewStore";
const validateFileSize = (file) => {
  const maxSize = 10 * 1024 * 1024; // 10MB for documents
  if (file.size > maxSize) {
    enqueueSnackbar("File size must be below 10 MB", { variant: "error" });
    return false;
  }
  return true;
};

const allowedTypes = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword",
  "text/plain",
];

const validateFileType = (file) => {
  return allowedTypes.includes(file.type);
};

const FileCellRenderer = ({
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  setFileUrl,
  onEditCell,
  params,
  onCellValueChanged,
  editable = false,
  valueInfos,
}) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isPending] = useState(false);
  const [fileName, setFileName] = useState("");
  const [fileType, setFileType] = useState("");
  const { dataset } = useParams();
  const { experimentId } = useParams();
  const [openMediaUpload, setOpenMediaUpload] = useState(false);
  useEffect(() => {
    if (value) {
      if (typeof value === "string") {
        setFileName(value?.split("/")?.pop() || value);
        setFileType(getFileType(value?.split(".")?.pop()));
        if (!value?.startsWith("data:")) {
          if (
            valueInfos?.documentName &&
            value?.split(".")?.pop()?.includes("/")
          ) {
            const mimeMatch = valueInfos?.documentName.match(/data:([^;]+)/);
            if (mimeMatch) {
              const mimeType = mimeMatch[1];
              setFileType(getFileType(getFileTypeFromMime(mimeType)));
            }
          }
        }
      }
    }
  }, [value, valueInfos?.documentName]);

  const processFile = (file) => {
    if (typeof file === "string") {
      onCellValueChanged({
        ...params,
        newValue: file,
        fileName: file,
      });
      return;
    }
    if (!validateFileSize(file) || !validateFileType(file)) {
      return;
    }
    if (file?.name) {
      setFileName(file?.name);
    }

    const reader = new FileReader();
    reader.onloadend = () => {
      const base64String = reader.result;
      onCellValueChanged({
        ...params,
        newValue: base64String,
        fileName: file?.name,
      });
    };
    reader.readAsDataURL(file);
  };

  const onDrop = (acceptedFiles, rejectedFiles) => {
    if (rejectedFiles.length > 0) {
      enqueueSnackbar("File must be PDF, DOCX, DOC, or TXT format", {
        variant: "error",
      });
      return;
    }

    if (acceptedFiles.length > 0) {
      const file = acceptedFiles[0];
      processFile(file);
    }
  };

  // Only enable dropzone when there's no existing file
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        [".docx"],
      "application/msword": [".doc"],
      "text/plain": [".txt"],
    },
    multiple: false,
    noClick: true,
    noKeyboard: true,
    disabled: Boolean(value), // Disable when there's already a file
  });

  const handleDelete = () => {
    try {
      onCellValueChanged({ ...params, newValue: null });
      setShowDeleteDialog(false);
    } catch (err) {
      logger.error("An error occurred while deleting the file:", err);
    }
  };

  const handleFileClick = (e) => {
    e.stopPropagation();
    if (setFileUrl) {
      setFileUrl(value);
    }
  };

  const theme = useTheme();
  const setOpenPreviewPdfDrawer = usePdfPreviewStoreShallow(
    (state) => state.setOpenPreviewPdfDrawer,
  );
  const onPreviewClick = () => {
    setOpenPreviewPdfDrawer({
      isPublic: true,
      name: fileName,
      type: fileType,
      url: dataset ? valueInfos?.documentUrl : valueInfos?.cellValue,
    });
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
            padding: theme.spacing(1.75, 1),
            position: "relative",
            "&:hover .icon-actions": {
              opacity: 1,
            },
          }}
        >
          {value ? (
            <Box
              sx={{
                height: "100%",
                width: "100%",
                display: "flex",
                alignItems: "center",
                cursor: "pointer",
                borderRadius: "8px",
                padding: theme.spacing(2, 1.5),
                border: "1px solid",
                borderColor: theme.palette.divider,
                backgroundColor: theme.palette.background.paper,
                position: "relative",
                "&:hover": {
                  backgroundColor: theme.palette.background.default,
                },
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  width: "80%",
                }}
                onClick={handleFileClick}
              >
                <Box
                  component={"img"}
                  sx={{
                    height: "20px",
                    width: "20px",
                  }}
                  alt="document icon"
                  src={getFileIcon(fileType, "pdf")}
                />
                <Stack gap={0.25} sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    variant="s2"
                    fontWeight={"fontWeightMedium"}
                    noWrap
                    color={"text.primary"}
                  >
                    {fileName}
                  </Typography>
                  <Typography
                    variant="s2"
                    fontWeight={"fontWeightRegular"}
                    color={"text.primary"}
                  >
                    {fileType}
                  </Typography>
                </Stack>
              </Box>
              {experimentId && value && (
                <Box
                  className="icon-actions"
                  sx={{
                    position: "absolute",
                    top: 4,
                    right: 4,
                    display: "flex",
                    height: "30px",
                    opacity: 0,
                    backgroundColor: theme.palette.background.paper,
                    borderRadius: "4px",
                    padding: "2px",
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <IconButton
                    onClick={(e) => {
                      e.stopPropagation();
                      window["__fileClick"] = true;
                      onPreviewClick();
                    }}
                  >
                    <SvgColor
                      sx={{ width: 16, color: "text.primary" }}
                      src="/assets/icons/ic_preveiew.svg"
                    />
                  </IconButton>
                </Box>
              )}
              {dataset && value && editable && (
                <Box
                  className="icon-actions"
                  sx={{
                    position: "absolute",
                    top: 4,
                    right: 4,
                    display: "flex",
                    height: "30px",
                    opacity: 0,
                    backgroundColor: theme.palette.background.paper,
                    borderRadius: "4px",
                    padding: "2px",
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <IconButton
                    onClick={(e) => {
                      e.stopPropagation();
                      window.__fileClick = true;
                      onPreviewClick();
                    }}
                  >
                    <SvgColor
                      sx={{ width: 16, color: "text.primary" }}
                      src="/assets/icons/ic_preveiew.svg"
                    />
                  </IconButton>
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      window.__fileClick = true;
                      onEditCell?.({
                        fileName,
                        fileType,
                        setFileName,
                        setFileType,
                      });
                    }}
                    aria-label="edit"
                  >
                    <SvgColor
                      src={`/assets/icons/ic_edit.svg`}
                      sx={{
                        width: 16,
                        height: 16,
                        color: theme.palette.text.primary,
                        PointerEvents: "none",
                      }}
                    />
                  </IconButton>

                  {value && (
                    <IconButton
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        window.__fileClick = true;
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
                          PointerEvents: "none",
                        }}
                      />
                    </IconButton>
                  )}
                </Box>
              )}
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
                        window.__fileClick = true;
                        setOpenMediaUpload(true);
                      }}
                    >
                      Click here
                    </label>{" "}
                    to upload file or <br />
                    drop a file here
                    <br />
                    <Typography
                      variant="caption"
                      component="span"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontSize: "0.75rem",
                      }}
                    >
                      (PDF, DOCX, DOC, TXT)
                    </Typography>
                  </Typography>
                </Box>
              </Box>
            )
          )}

          <RenderMeta originType={originType} meta={metadata} />
        </Box>
      </CustomTooltip>
      <DeleteMediaDialog
        open={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        onDelete={handleDelete}
        isPending={isPending}
        fileName={fileName}
        fileType="file"
      />
      <ReplaceMediaDialog
        type="upload"
        open={openMediaUpload}
        onClose={() => setOpenMediaUpload(false)}
        onUpload={(value) => {
          processFile(value);
        }}
      />
    </>
  );
};

FileCellRenderer.propTypes = {
  ...commonPropTypes,
};

export default React.memo(FileCellRenderer);
