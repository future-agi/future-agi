import PropTypes from "prop-types";
import {
  Alert,
  Dialog,
  DialogContent,
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
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
import { useDeleteColumnStore } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { notify } from "src/sections/common/ux/toast";
import { useCreatedColumnsStore } from "./Versions/changeSetStore";
import { runColumnMutation } from "./columnMutationRequest";

const ConfirmDeleteColumn = ({ dataset: datasetId }) => {
  const { deleteColumn, setDeleteColumn } = useDeleteColumnStore();
  const { dataset: urlDataset } = useParams();
  const dataset = urlDataset ?? datasetId;
  const { refreshGrid } = useDevelopDetailContext();
  const { data: datasetList } = useDevelopDatasetList();
  const queryClient = useQueryClient();
  const currentDataset = datasetList?.find((v) => v.datasetId === dataset);

  const {
    mutate: deleteColumnMutate,
    isPending: isLoading,
    error,
    reset: resetMutation,
  } = useMutation({
    networkMode: "always",
    meta: { errorHandled: true },
    mutationFn: (d) =>
      runColumnMutation((config) =>
        axios.delete(endpoints.develop.deleteColumn(dataset, d), config),
      ),
    onSuccess: () => {
      trackEvent(Events.deleteColumnSuccessful, {
        [PropertyName.columnType]: {
          dataset: currentDataset?.name,
          columnId: deleteColumn.id,
          deleted_column_name: deleteColumn.name,
        },
      });
      // integration (DV-01): say what happened; a column added with the pending
      // changes is no longer offered to Discard.
      if (deleteColumn?.id)
        useCreatedColumnsStore.getState().remove(dataset, deleteColumn.id);
      notify(`Column “${deleteColumn?.name ?? ""}” deleted`, {
        variant: "success",
        key: "column-deleted",
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
    if (isLoading) return;
    resetMutation();
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
            {`Delete column “${deleteColumn?.name ?? ""}”?`}
          </Typography>
          <IconButton
            aria-label="Close delete column"
            disabled={isLoading}
            onClick={onClose}
          >
            <Iconify icon="mdi:close" />
          </IconButton>
        </Box>
        {/* integration (DV-01): columns are not versioned; say what goes. */}
        <Typography typography="s1" color="text.secondary">
          Deleting this column removes it and its values from the current dataset.
          This can&apos;t be undone.
        </Typography>
      </DialogTitle>
      {error && (
        <DialogContent>
          <Alert severity="error">
            {getSafeActionErrorMessage(error, "Couldn't delete the column")}
          </Alert>
        </DialogContent>
      )}
      <DialogActions sx={{ padding: 2 }}>
        <Button
          onClick={onClose}
          disabled={isLoading}
          variant="outlined"
          size="small"
        >
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
