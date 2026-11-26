import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  useTheme,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

const DeleteProject = ({
  open,
  onClose,
  projectId,
  projectName,
  projectType = "experiment",
  onSuccess,
}) => {
  const theme = useTheme();

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      axios.delete(endpoints.project.deleteObservePrototype, {
        data: {
          project_ids: [projectId],
          project_type: projectType,
        },
      }),
    onSuccess: () => {
      enqueueSnackbar(`${projectName} has been deleted`, {
        variant: "success",
      });
      onSuccess();
      onClose();
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: 560,
          maxHeight: 329,
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      <DialogTitle
        id="configure-dialog-title"
        sx={{
          paddingTop: theme.spacing(1.5),
          paddingBottom: theme.spacing(0.5),
          paddingX: theme.spacing(1.5),
        }}
      >
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
            Delete Project
          </Box>
          <IconButton
            size="small"
            aria-label="close-configure-project"
            onClick={onClose}
          >
            <Iconify icon="solar:close-linear" color="text.primary" />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent
        sx={{ paddingX: theme.spacing(1.5), color: "text.disabled" }}
      >
        Are you sure you want to delete this project?
      </DialogContent>
      <DialogActions
        sx={{ paddingX: theme.spacing(1.5), paddingBottom: theme.spacing(2) }}
      >
        <Button
          size="small"
          onClick={onClose}
          aria-label="Cancel-configure-project"
          variant="outlined"
          color="inherit"
          sx={{
            width: "90px",
            color: "text.disabled",
          }}
        >
          Cancel
        </Button>

        <LoadingButton
          size="small"
          aria-label="update-project"
          onClick={() => mutate()}
          disabled={isPending}
          sx={{
            width: "90px",
          }}
          variant="contained"
          color="error"
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DeleteProject.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  projectId: PropTypes.string,
  projectName: PropTypes.string,
  projectType: PropTypes.oneOf(["observe", "experiment"]),
  onSuccess: PropTypes.func,
};

export default DeleteProject;
