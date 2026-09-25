import { Box, DialogActions, Stack, Typography, useTheme } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { getFileIcon } from "src/sections/knowledge-base/sheet-view/icons";
import SvgColor from "src/components/svg-color";
import { LoadingButton } from "@mui/lab";
import DeleteMediaDialog from "./ConfirmDelete";
import ReplaceMediaDialog from "./ReplaceMediaDialog";
import _ from "lodash";
import { getFileType } from "../../../common/DevelopCellRenderer/CellRenderers/common";
import {
  isDocumentWebAddress,
  NOT_A_WEB_ADDRESS_MESSAGE,
} from "./editHelper";

const getTempFileData = (tempFile) => {
  if (tempFile instanceof File) {
    return {
      type: _.upperCase(tempFile?.name.split(".").pop()),
      name: tempFile?.name,
    };
  } else {
    return {
      type: _.upperCase(tempFile.split(".").pop()),
      name: tempFile.split("/").pop(),
    };
  }
};

export default function EditFile({ params, onClose, onCellValueChanged }) {
  const theme = useTheme();
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [openReplaceDialog, setOpenReplaceDialog] = useState(false);
  const [tempFile, setTempFile] = useState(null);

  const {
    fileName = "",
    fileType = "",
    setFileName = () => {},
    setFileType = () => {},
  } = params;

  const handleClose = () => {
    onClose();
    setTempFile(null);
  };

  const onSubmit = (e) => {
    e.preventDefault();

    try {
      // Case 1: User selected a new file (tempFile exists)
      const { name, type } = getTempFileData(tempFile);
      if (tempFile instanceof File) {
        // Process the file to base64
        const reader = new FileReader();
        reader.onloadend = () => {
          const base64String = reader.result;

          // Update cell with new file
          onCellValueChanged({
            ...params,
            newValue: base64String,
            fileName: name,
            onSuccess: () => {
              enqueueSnackbar("File has been updated", { variant: "success" });
              setFileName(name);
              setFileType(getFileType(type));
            },
          });

          // Update filename and file type

          handleClose();
        };

        reader.onerror = () => {
          enqueueSnackbar("Error reading file", { variant: "error" });
        };

        reader.readAsDataURL(tempFile);
      } else if (typeof tempFile === "string") {
        if (!isDocumentWebAddress(tempFile)) {
          enqueueSnackbar(NOT_A_WEB_ADDRESS_MESSAGE, { variant: "error" });
          return;
        }
        onCellValueChanged({
          ...params,
          newValue: tempFile,
          fileName: name,
          onSuccess: () => {
            setFileName(name);
            setFileType(getFileType(type));
            handleClose();
          },
          onError: () => {
            setTempFile(null);
          },
        });
      }
      // Case 2: No new file selected, just closing/saving current state
      else {
        handleClose();
      }
    } catch (err) {
      enqueueSnackbar("Error updating document", {
        variant: "error",
      });
    }
  };

  const handleConfirmDelete = () => {
    if (tempFile) {
      setTempFile(null);
    }
    setIsDeleteDialogOpen(false);
    onCellValueChanged({ ...params, newValue: null, fileName: null });
    handleClose();
  };

  const actionButtons = [
    {
      icon: "ic_replace",
      title: "Replace",
      action: () => setOpenReplaceDialog(true),
    },
    {
      icon: "ic_delete",
      title: "Delete",
      action: () => setIsDeleteDialogOpen(true),
    },
  ];

  const buttonStyles = {
    color: "text.primary",
    fontSize: "12px",
    fontWeight: 400,
    lineHeight: "18px",
  };

  const iconStyles = {
    width: 16,
    height: 16,
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
          gap: theme.spacing(4),
          minWidth: "400px",
        }}
        component="form"
        onSubmit={onSubmit}
      >
        <Box
          sx={{
            padding: theme.spacing(1.5),
            border: "1px solid",
            borderColor: "divider",
            borderRadius: theme.spacing(1),
          }}
        >
          <Stack
            direction={"row"}
            alignItems={"flex-start"}
            gap={theme.spacing(1.5)}
          >
            <Box
              component={"img"}
              sx={{
                height: "20px",
                width: "20px",
              }}
              alt="document icon"
              src={getFileIcon(
                tempFile
                  ? _.toLower(getTempFileData(tempFile)?.type)
                  : fileType,
                "pdf",
              )}
            />
            <Stack direction={"column"} gap={theme.spacing(0.25)}>
              <Typography
                variant="s2"
                color={"text.primary"}
                fontWeight={"fontWeightMedium"}
              >
                {tempFile ? getTempFileData(tempFile)?.name : fileName}
              </Typography>
              <Typography
                variant="s3"
                color={"text.disabled"}
                fontWeight={"fontWeightRegular"}
              >
                {tempFile
                  ? getTempFileData(tempFile)?.type
                  : _.upperCase(fileType)}
              </Typography>
            </Stack>
            <Box sx={{ display: "flex", gap: 1, ml: "auto", mt: -1 }}>
              {actionButtons.map((button, index) => (
                <LoadingButton
                  key={index}
                  size="small"
                  startIcon={
                    <SvgColor
                      src={`/assets/icons/components/${button.icon}.svg`}
                      sx={iconStyles}
                    />
                  }
                  onClick={button?.action}
                  sx={buttonStyles}
                >
                  {button.title}
                </LoadingButton>
              ))}
            </Box>
          </Stack>
        </Box>
        <DialogActions sx={{ justifyContent: "flex-end", padding: 0 }}>
          <LoadingButton
            variant="contained"
            type="submit"
            onClick={onSubmit}
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
        fileName={tempFile ? getTempFileData(tempFile)?.name : fileName}
        fileType="file"
      />
      <ReplaceMediaDialog
        open={openReplaceDialog}
        onClose={() => setOpenReplaceDialog(false)}
        onUpload={(value) => {
          setTempFile(value);
        }}
      />
    </>
  );
}

EditFile.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
