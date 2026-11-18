import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import Iconify from "src/components/iconify";
import { z } from "zod";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const UpdateDatasetModalForm = ({ currentDataset, onClose }) => {
  const { dataset } = useParams();

  const { control, handleSubmit } = useForm({
    defaultValues: { datasetName: currentDataset },
    resolver: zodResolver(
      z.object({ datasetName: z.string().min(1, "Dataset name is required") }),
    ),
  });

  const queryClient = useQueryClient();

  const { mutate: updateDataset, isPending } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
    onSuccess: () => {
      enqueueSnackbar("Dataset updated successfully", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      onClose();
    },
  });

  const onSubmit = (data) => {
    // @ts-ignore
    updateDataset({ dataset_name: data.datasetName });
    trackEvent(Events.updateDatasetSuccess, {
      [PropertyName.meta]: {
        oldDatasetName: currentDataset,
        newDatasetName: data.datasetName,
      },
    });
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <DialogContent
        sx={{
          paddingTop: 0.5,
        }}
      >
        <FormTextFieldV2
          autoFocus
          label="Dataset Name"
          placeholder="Enter dataset name"
          size="small"
          control={control}
          fieldName="datasetName"
          fullWidth
        />
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          loading={isPending}
          type="submit"
        >
          Save
        </LoadingButton>
      </DialogActions>
    </form>
  );
};

UpdateDatasetModalForm.propTypes = {
  currentDataset: PropTypes.string,
  onClose: PropTypes.func,
};

const UpdateDatasetModal = ({ open, onClose }) => {
  const { dataset } = useParams();
  const { data } = useDevelopDatasetList();

  const currentDataset = data?.find((d) => d.datasetId === dataset)?.name;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
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
          <Typography color={"text.primary"} fontWeight={600} fontSize="16px">
            Update Dataset Details
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
      {currentDataset?.length && (
        <UpdateDatasetModalForm
          currentDataset={currentDataset}
          onClose={onClose}
        />
      )}
    </Dialog>
  );
};

UpdateDatasetModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default UpdateDatasetModal;
