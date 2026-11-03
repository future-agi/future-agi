import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
  Stack,
  Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { getFileIcon } from "./icons";
import { NUMBER_OF_ROWS_THAT_CAN_BE_SELECTED } from "./utils";

const DeleteItem = (props) => {
  const { name, fileSize, onDelete, isLoading } = props;

  const fileType = name?.split(".").pop();
  const iconSrc = getFileIcon(fileType);
  return (
    <Stack
      width={"100%"}
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"flex-start"}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        padding: "12px",
      }}
    >
      <Stack direction={"row"} gap={"8px"}>
        <Box
          component={"img"}
          sx={{
            height: "16px",
            width: "16px",
          }}
          alt="document icon"
          src={iconSrc}
        />
        <Stack>
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            {name}
          </Typography>
          <Typography
            variant="s2"
            color={"text.disabled"}
            fontWeight={"fontWeightRegular"}
          >
            {fileSize}
          </Typography>
        </Stack>
      </Stack>
      <IconButton
        onClick={onDelete}
        disabled={isLoading}
        sx={{
          color: "text.primary",
          padding: 0,
          margin: 0,
        }}
      >
        <Iconify
          sx={{
            height: "16px",
            width: "16px",
          }}
          icon="solar:close-linear"
        />
      </IconButton>
    </Stack>
  );
};

export default function ConfirmDelete({
  open,
  onClose,
  onConfirm,
  isLoading,
  selectedFiles,
  handleRemoveSelectedFile,
  selectedAll,
  totalRows,
  excludingIds,
}) {
  const fileCount = selectedAll
    ? totalRows - (excludingIds?.length ?? 0)
    : selectedFiles?.length ?? 0;

  const confirmationMessage = `Are you sure you want to delete ${
    fileCount > 1 ? "these" : "this"
  } file${fileCount > 1 ? "s" : ""}?`;

  const deleteMessage = `Delete ${fileCount} file${fileCount !== 1 ? "s" : ""}`;

  return (
    <Dialog
      open={open}
      onClose={isLoading ? () => {} : onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
      fullWidth
      maxWidth={false}
      sx={{}}
      PaperProps={{
        sx: {
          maxWidth: 400,
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          rowGap: "12px",
          maxHeight: "400px",
        },
      }}
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 0,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
          }}
        >
          <Stack direction={"column"} gap={"2px"}>
            <Typography
              variant="m3"
              color={"text.primary"}
              fontWeight={"fontWeightBold"}
            >
              {deleteMessage}
            </Typography>
            <Typography
              variant="s1"
              color={"text.secondary"}
              fontWeight={"fontWeightRegular"}
            >
              {confirmationMessage}
            </Typography>
          </Stack>
          <IconButton
            disabled={isLoading}
            sx={{
              color: "text.primary",
            }}
            onClick={onClose}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
      <Divider
        flexItem
        sx={{
          borderColor: "divider",
        }}
      />
      <DialogContent sx={{ display: "flex", flexWrap: "wrap", padding: 0 }}>
        <Stack gap={"12px"} direction={"column"} width={"100%"}>
          {selectedFiles?.length <= NUMBER_OF_ROWS_THAT_CAN_BE_SELECTED &&
            selectedFiles?.map((item, index) => (
              <DeleteItem
                onDelete={() => handleRemoveSelectedFile(item?.id)}
                key={index}
                isLoading={isLoading}
                {...item}
              />
            ))}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ padding: 2, pb: 0 }}>
        <Button
          sx={{
            color: "text.disabled",
            minWidth: "19px",
          }}
          disabled={isLoading}
          onClick={onClose}
          variant="outlined"
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightMedium"}
            fontSize={"13px"}
          >
            Cancel
          </Typography>
        </Button>
        <LoadingButton
          loading={isLoading}
          onClick={onConfirm}
          variant="contained"
          autoFocus
          color="error"
          sx={{
            backgroundColor: "status.danger.solid",
            color: "common.white",
            minWidth: "19px",
          }}
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            fontSize={"13px"}
          >
            Delete
          </Typography>
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

ConfirmDelete.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  isLoading: PropTypes.bool,
  selectedFiles: PropTypes.array,
  handleRemoveSelectedFile: PropTypes.func,
  totalRows: PropTypes.number,
  selectedAll: PropTypes.bool,
  excludingIds: PropTypes.array,
};

DeleteItem.propTypes = {
  name: PropTypes.string,
  fileSize: PropTypes.string,
  onDelete: PropTypes.func,
  isLoading: PropTypes.bool,
};
