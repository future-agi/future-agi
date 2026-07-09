import { zodResolver } from "@hookform/resolvers/zod";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useForm } from "react-hook-form";
import { CloneDevelopDatasetValidationSchema } from "./validation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
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
import Iconify from "src/components/iconify";
import { FormSelectField } from "src/components/FormSelectField";
import { LoadingButton } from "@mui/lab";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { useNavigate } from "react-router";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const CloneDevelopDataset = ({ open, onClose, refreshGrid }) => {
  const { control, handleSubmit, reset } = useForm({
    defaultValues: {
      name: "",
      model_type: "GenerativeLLM",
      new_dataset_id: "",
    },
    resolver: zodResolver(CloneDevelopDatasetValidationSchema),
  });

  const queryClient = useQueryClient();

  const onCloseClick = () => {
    onClose();
    reset();
  };

  const navigate = useNavigate();

  const { mutate: cloneDataset, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.cloneDataset(data.new_dataset_id),
        {
          model_type: data?.model_type,
          new_dataset_name: data?.name,
        },
        {
          headers: { "Content-Type": "multipart/form-data" },
        },
      ),
    onSuccess: (data) => {
      trackEvent(Events.dataAddSuccessfull, {
        [PropertyName.method]: "add from existing dataset",
      });
      onCloseClick();
      enqueueSnackbar("Dataset created successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      navigate(`/dashboard/develop/${data?.data?.result?.dataset_id}?tab=data`);
      refreshGrid();
    },
  });

  const onSubmit = (data) => {
    // Find the selected dataset's name

    //@ts-ignore
    cloneDataset(data);
  };

  const { data: datasetList } = useDevelopDatasetList();

  const datasetOptions = useMemo(
    () =>
      datasetList?.map(({ datasetId, name }) => ({
        label: name,
        value: datasetId,
      })),
    [datasetList],
  );

  return (
    <Dialog open={open} onClose={onCloseClick} maxWidth="xs" fullWidth>
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
          <Typography fontWeight={700} fontSize="18px">
            Add from existing dataset
          </Typography>
          <IconButton onClick={onCloseClick}>
            <Iconify icon="mdi:close" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          paddingTop: 0.5,
        }}
      >
        <FormTextFieldV2
          fieldName="name"
          control={control}
          size="small"
          label="Name"
          fullWidth
          placeholder="Enter name"
          rules={{ required: "Name is required" }}
        />
        <FormSelectField
          options={datasetOptions}
          label="Select Dataset"
          control={control}
          fieldName="new_dataset_id"
          fullWidth
          size="small"
          MenuProps={{
            PaperProps: {
              sx: {
                maxHeight: 224,
              },
            },
          }}
        />
        {/* <FormSelectField
          size="small"
          label="Model type"
          control={control}
          fieldName="model_type"
          options={[
            { label: "Generative LLM", value: "GenerativeLLM" },
            { label: "Generative Image", value: "GenerativeImage" },
          ]}
          MenuProps={{
            PaperProps: {
              sx: {
                maxHeight: 224,
              },
            },
          }}
        /> */}
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onCloseClick} variant="outlined">
          Cancel
        </Button>
        <LoadingButton
          onClick={handleSubmit(onSubmit)}
          variant="contained"
          autoFocus
          color="primary"
          loading={isPending}
        >
          Done
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

CloneDevelopDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default CloneDevelopDataset;
