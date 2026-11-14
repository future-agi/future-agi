import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
import { useDeleteColumnStore } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";

const ConfirmDeleteColumn = ({ dataset: datasetId }) => {
  const { deleteColumn, setDeleteColumn } = useDeleteColumnStore();
  const { dataset: urlDataset } = useParams();
  const dataset = urlDataset ?? datasetId;
  const { refreshGrid } = useDevelopDetailContext();
  const { data: datasetList } = useDevelopDatasetList();
  const queryClient = useQueryClient();
  const currentDataset = datasetList?.find((v) => v.datasetId === dataset);

  const { mutate: deleteColumnMutate, isPending: isLoading } = useMutation({
    mutationFn: (d) => axios.delete(endpoints.develop.deleteColumn(dataset, d)),
    onSuccess: () => {
      trackEvent(Events.deleteColumnSuccessful, {
        [PropertyName.columnType]: {
          dataset: currentDataset?.name,
          columnId: deleteColumn.id,
          deleted_column_name: deleteColumn.name,
        },
      });
      setDeleteColumn(null);
      refreshGrid();
      queryClient.invalidateQueries({
        queryKey: ["json-column-schema", dataset],
      });
    },
  });

  const onConfirm = () => {
    deleteColumnMutate(deleteColumn?.id);
  };

  const onClose = () => {
    setDeleteColumn(null);
  };

  return (
    <Dialog
      open={Boolean(deleteColumn)}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography variant="h6">
            Are you sure you want to delete this column?
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined" size="small">
          Cancel
        </Button>
        <LoadingButton
          onClick={onConfirm}
          variant="contained"
          autoFocus
          size="small"
          color="error"
          loading={isLoading}
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ConfirmDeleteColumn.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  isLoading: PropTypes.bool,
  dataset: PropTypes.string,
};

export default ConfirmDeleteColumn;
