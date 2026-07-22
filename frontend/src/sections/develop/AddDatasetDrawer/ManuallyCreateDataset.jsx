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
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { ManuallyCreateDatasetValidationSchema } from "./validation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { useNavigate } from "react-router";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { getCreatedDatasetId } from "./manualCreateDatasetResponse";

const ManuallyCreateDataset = ({ open, onClose, refreshGrid }) => {
  const defaultValues = {
    name: "",
    modelType: "GenerativeLLM",
    number_of_rows: 1,
    number_of_columns: 1,
  };
  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid },
  } = useForm({
    defaultValues,
    resolver: zodResolver(ManuallyCreateDatasetValidationSchema),
    mode: "onChange",
  });

  const queryClient = useQueryClient();

  const onCloseClick = () => {
    onClose();
    reset();
  };

  const navigate = useNavigate();

  const { mutate: createEmptyDataset, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.createDatasetManually, data, {
        headers: { "Content-Type": "application/json" },
      }),
    onSuccess: (data) => {
      onCloseClick();
      trackEvent(Events.dataAddSuccessfull, {
        [PropertyName.method]: "add using manually create dataset",
      });
      enqueueSnackbar("Dataset created successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      const datasetId = getCreatedDatasetId(data);
      if (datasetId) {
        navigate(`/dashboard/develop/${datasetId}?tab=data`);
      }
      refreshGrid();
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create dataset", {
          retryAction: "creating this dataset",
        }),
        { variant: "error" },
      );
    },
  });

  const onSubmit = (data) => {
    //@ts-ignore
    createEmptyDataset({
      dataset_name: data?.name,
      model_type: data?.modelType,
      number_of_rows: data?.number_of_rows,
      number_of_columns: data?.number_of_columns,
    });
    trackEvent(Events.datasetManualAdditionSuccessful, {
      [PropertyName.name]: data.name,
      [PropertyName.rowCount]: data.row_count || 0,
      [PropertyName.columnCount]: data.column_count || 0,
    });
  };

  return (
    <Dialog open={open} onClose={onCloseClick} maxWidth="sm">
      <Box sx={{ padding: 2 }} width="480px">
        <DialogTitle
          sx={{
            display: "flex",
            flexDirection: "column",
            padding: 0,
            margin: 0,
            gap: "2px",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              variant="m2"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Add Dataset Manually
            </Typography>
            <IconButton onClick={onCloseClick}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Add multiple empty rows and columns to your dataset" />
        </DialogTitle>

        <DialogContent
          sx={{ display: "flex", flexDirection: "column", gap: 2, padding: 0 }}
        >
          <Box
            paddingTop={2}
            sx={{ display: "flex", flexDirection: "column", gap: 2 }}
          >
            <FormTextFieldV2
              fieldName="name"
              placeholder="Name"
              control={control}
              label="Dataset Name"
              size="small"
              fullWidth
              required
              rules={{ required: "Name is required" }}
            />
            <FormTextFieldV2
              fieldName="number_of_rows"
              placeholder="Number of rows"
              control={control}
              label="No. of Rows"
              size="small"
              fieldType="number"
              defaultValue={defaultValues.number_of_rows}
              fullWidth
              required
              rules={{ required: "No of rows are required" }}
              isSpinnerField={true}
            />
            <FormTextFieldV2
              fieldName="number_of_columns"
              placeholder="Number of columns"
              control={control}
              label="No. of Columns"
              size="small"
              fieldType="number"
              defaultValue={defaultValues.number_of_columns}
              fullWidth
              required
              isSpinnerField={true}
              rules={{ required: "No of columns are required" }}
            />
          </Box>
          {/* <FormSelectField
            size="small"
            label="Model type"
            control={control}
            fieldName="modelType"
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
        <DialogActions sx={{ padding: 0, marginTop: 4 }}>
          <Button
            onClick={onCloseClick}
            variant="outlined"
            sx={{
              minWidth: "90px",
              // borderRadius: "8px",
              // padding: "6px 24px"
            }}
          >
            <Typography
              variant="s2"
              fontWeight={"fontWeightSemiBold"}
              color="text.secondary"
            >
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            onClick={handleSubmit(onSubmit)}
            variant="contained"
            autoFocus
            color="primary"
            loading={isPending}
            disabled={!isValid}
            sx={{
              minWidth: "90px",
              "&:disabled": {
                color: "common.white",
                backgroundColor: "action.hover",
              },
              // borderRadius: "8px",
              // padding: "6px 24px"
            }}
          >
            <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
              Save
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

ManuallyCreateDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default ManuallyCreateDataset;
