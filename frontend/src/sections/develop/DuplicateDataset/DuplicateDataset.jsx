import { LoadingButton } from "@mui/lab";
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
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { getClonedDatasetInfo } from "./duplicateDatasetResponse";

const DuplicateDataset = ({ open, onClose, refreshGrid, selected }) => {
  const {
    control,
    handleSubmit: handleFormSubmit,
    reset,
    watch,
  } = useForm({
    defaultValues: {
      name: "",
    },
  });

  const fieldName = watch("name");

  const { mutate, isPending } = useMutation({
    mutationFn: (data) =>
      axios
        .post(endpoints.develop.cloneDataset(selected?.id), data)
        .then((res) => res.data),
    onSuccess: (data) => {
      const clonedDataset = getClonedDatasetInfo(data);
      trackEvent(Events.duplicateDatasetClicked, {
        [PropertyName.meta]: {
          datasetId: clonedDataset.datasetId,
          datasetName: clonedDataset.datasetName,
        },
      });
      enqueueSnackbar(`${fieldName} dataset has been created`, {
        variant: "success",
      });
      handleClose();
      refreshGrid();
    },
  });

  const onSubmitCreateColumn = (data) => {
    if (!data.name) {
      enqueueSnackbar("Please enter a name", { variant: "error" });
      return;
    }
    const formData = new FormData();
    formData.append("model_type", selected.datasetType);
    formData.append("new_dataset_name", data.name);
    mutate(formData);
  };

  const handleClose = () => {
    reset();
    onClose();
  };
  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0, gap: "2px" }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="m2"
            >
              Duplicate Dataset
            </Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Create a new name for the dataset that you want to duplicate" />
        </DialogTitle>
        <Box component="form" onSubmit={handleFormSubmit(onSubmitCreateColumn)}>
          <DialogContent
            sx={{
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: "20px",
              paddingTop: 2,
            }}
          >
            <FormTextFieldV2
              autoFocus
              placeholder="Enter dataset name"
              label="Enter Dataset Name"
              size="small"
              control={control}
              fieldName="name"
              fullWidth
            />
          </DialogContent>
          <DialogActions sx={{ padding: 0, marginTop: 4 }}>
            <Button onClick={handleClose} variant="outlined">
              <Typography
                variant="s2"
                fontWeight={"fontWeightSemiBold"}
                color="text.secondary"
              >
                Cancel
              </Typography>
            </Button>
            <LoadingButton
              variant="contained"
              color="primary"
              loading={isPending}
              type="submit"
            >
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                Create
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DuplicateDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.any,
};

export default DuplicateDataset;
