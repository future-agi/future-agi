import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

const DeleteRuns = ({ open, onClose, selectedRows, refreshGrid }) => {
  const [runsToDelete, setRunsToDelete] = useState([]);
  const theme = useTheme();

  useEffect(() => {
    if (open) {
      setRunsToDelete(selectedRows);
    }
  }, [selectedRows, open]);

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      axios.post(endpoints.project.deleteRuns(), {
        ids: runsToDelete.map((row) => row.id),
      }),
    onSuccess: () => {
      const runCount = runsToDelete.length;
      const message = `${runCount} run${runCount > 1 ? "s" : ""} deleted successfully`;
      enqueueSnackbar(message, { variant: "success" });
      onClose();
      refreshGrid();
    },
  });

  const handleRemoveRun = (id) => {
    setRunsToDelete((prev) => {
      const updated = prev.filter((run) => run.id !== id);
      if (updated.length === 0) {
        onClose();
      }
      return updated;
    });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="xs"
      aria-labelledby="delete-dialog-title"
      PaperProps={{
        sx: {
          width: 480,
          maxHeight: 329,
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      <DialogTitle
        id="delete-dialog-title"
        sx={{
          paddingTop: theme.spacing(1.5),
          paddingBottom: theme.spacing(0.5),
          paddingX: theme.spacing(1.5),
        }}
      >
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
            Delete {runsToDelete.length} Run{runsToDelete.length !== 1 && "s"}
          </Box>
          <IconButton
            aria-label="close-delete-run"
            onClick={onClose}
            sx={{
              p: 0,
            }}
          >
            <Iconify icon="line-md:close" color="text.primary" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent
        sx={{
          overflow: "hidden",
          flexGrow: 1,
          paddingX: theme.spacing(1.5),
          mb: theme.spacing(1),
        }}
      >
        <Typography
          mb={theme.spacing(1)}
          fontWeight={400}
          color="text.secondary"
          fontSize={14}
        >
          Are you sure you want to delete the following run
          {runsToDelete.length !== 1 && "s"}?
        </Typography>
        <Divider />
        <Stack
          spacing={theme.spacing(1)}
          sx={{
            maxHeight: 160,
            overflowY: "auto",
            mt: theme.spacing(1.5),
            "&::-webkit-scrollbar": {
              width: "4px",
            },
            "&::-webkit-scrollbar-track": {
              backgroundColor: "transparent",
            },
            "&::-webkit-scrollbar-thumb": {
              backgroundColor: "action.disabledBackground",
              borderRadius: "4px",
            },
          }}
        >
          {runsToDelete.map((run) => (
            <Box
              key={run.id}
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              height={46}
              border={1}
              borderColor="divider"
              p={theme.spacing(1.5)}
              borderRadius="8px"
            >
              <Typography fontSize={14} fontWeight={500}>
                {run.runName}
              </Typography>
              <IconButton
                aria-label="remove-run"
                onClick={() => {
                  if (runsToDelete.length === 1) {
                    onClose();
                  } else {
                    handleRemoveRun(run.id);
                  }
                }}
                sx={{ width: 16, height: 16, p: 0 }}
              >
                <Iconify
                  icon="line-md:close"
                  color="text.primary"
                  width={16}
                  height={16}
                />
              </IconButton>
            </Box>
          ))}
        </Stack>
      </DialogContent>

      <DialogActions
        sx={{
          padding: theme.spacing(1.5),
        }}
      >
        <Button
          aria-label="Cancel-run-deletion"
          variant="outlined"
          color="inherit"
          onClick={onClose}
          sx={{
            width: "90px",
            height: "30px",
            fontSize: "12px",
            color: "text.disabled",
          }}
        >
          Cancel
        </Button>
        <LoadingButton
          aria-label="Confirm-delete-runs"
          type="submit"
          sx={{
            width: "90px",
            height: "30px",
            fontSize: "12px",
          }}
          loading={isPending}
          disabled={runsToDelete.length === 0}
          variant="contained"
          color="error"
          onClick={() => mutate()}
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DeleteRuns.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedRows: PropTypes.array,
  refreshGrid: PropTypes.func,
};

export default DeleteRuns;
