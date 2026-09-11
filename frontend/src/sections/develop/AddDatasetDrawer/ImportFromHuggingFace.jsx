import { LoadingButton } from "@mui/lab";
import {
  Alert,
  Box,
  Dialog,
  IconButton,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import Iconify from "src/components/iconify";
import Logo from "src/components/logo";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { zodResolver } from "@hookform/resolvers/zod";
import { HuggingFaceDatasetValidationSchema } from "./validation";
import { useNavigate } from "react-router";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { getRequestErrorMessage } from "src/utils/errorUtils";

const ImportFromHuggingFace = ({ open, onClose, refreshGrid }) => {
  const [searchText, setSearchText] = useState("");

  const queryClient = useQueryClient();

  const navigate = useNavigate();

  const {
    data: loadedDataset,
    mutate,
    isPending: isLoadingDataset,
    isError,
    isSuccess,
    error: loadDatasetError,
    reset: resetQuery,
  } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.getHuggingFaceDataset, data, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: () => {
      enqueueSnackbar("Dataset loaded successfully", {
        variant: "success",
      });
      refreshGrid();
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to load dataset", {
          retryAction: "loading this Hugging Face dataset",
        }),
        { variant: "error" },
      );
    },
  });

  const {
    control,
    handleSubmit,
    reset: resetForm,
  } = useForm({
    defaultValues: {
      model_type: "GenerativeLLM",
      new_dataset_name: "",
      huggingface_dataset_config: "",
      huggingface_dataset_split: "",
    },
    resolver: zodResolver(HuggingFaceDatasetValidationSchema),
  });

  const onCloseClick = () => {
    onClose();
    setSearchText("");
    resetQuery();
    resetForm();
  };

  const {
    mutate: createHuggingFaceDataset,
    isPending: isLoadingCreateDataset,
  } = useMutation({
    mutationFn: (d) =>
      axios.post(endpoints.develop.createHuggingFaceDataset, d, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: (data) => {
      enqueueSnackbar("Dataset created successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      navigate(`/dashboard/develop/${data?.data?.result?.dataset_id}?tab=data`);
      onCloseClick();
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create dataset", {
          retryAction: "creating this dataset from Hugging Face",
        }),
        { variant: "error" },
      );
    },
  });

  const onLoadDataset = (e) => {
    e.preventDefault();
    if (!searchText.length) return;
    //@ts-ignore
    mutate({ dataset_path: searchText });
  };

  const onSubmit = (data) => {
    // @ts-ignore
    createHuggingFaceDataset({
      ...data,
      huggingface_dataset_name: searchText,
    });
  };

  const subset = useWatch({ control, name: "huggingface_dataset_config" });

  const { subsetOptions, splitOptions } = useMemo(() => {
    let subsetOptions = [];
    let splitOptions = [];

    const datasetInfo = loadedDataset?.data?.result?.dataset_info?.splits;

    if (datasetInfo) {
      subsetOptions = Object.keys(datasetInfo)?.map((subset) => ({
        label: subset,
        value: subset,
      }));
    }

    if (subset?.length) {
      splitOptions = datasetInfo?.[subset]?.map((split) => ({
        label: split,
        value: split,
      }));
    }

    return { subsetOptions, splitOptions };
  }, [loadedDataset, subset]);

  return (
    <Dialog
      open={open}
      onClose={onCloseClick}
      maxWidth="xs"
      fullWidth
      fullScreen
    >
      <Box sx={{ position: "absolute", top: 10, right: 10 }}>
        <IconButton onClick={onCloseClick}>
          <Iconify icon="mdi:close" />
        </IconButton>
      </Box>
      <Box
        sx={{
          alignItems: "center",
          justifyContent: "center",
          display: "flex",
          height: "100%",
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
            alignItems: "center",
          }}
        >
          <Logo
            collapsed
            width={51}
            height={51}
            sx={{ width: "51px", height: "51px", marginBottom: 1.5 }}
          />
          <Typography fontWeight={700} fontSize="20px">
            Experiment with Hugging Face 🤗
          </Typography>
          <Typography fontSize="14px">
            Grab a HuggingFace dataset, and start experimenting in Future AGI
          </Typography>
        </Box>
        <form
          style={{
            paddingTop: "32px",
            display: "flex",
            gap: "8px",
            alignItems: "center",
          }}
          onSubmit={onLoadDataset}
        >
          <TextField
            variant="outlined"
            placeholder="Enter Hugging Face dataset id"
            label="HuggingFace Dataset ID"
            size="small"
            sx={{ minWidth: "480px" }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <LoadingButton
            variant="contained"
            size="small"
            loading={isLoadingDataset}
            sx={{
              height: "100%",
              backgroundColor: "action.hover",
              color: "text.primary",
              "&:hover": {
                backgroundColor: "action.selected",
              },
            }}
            type="submit"
          >
            Load Dataset
          </LoadingButton>
        </form>
        {isError && (
          <Alert
            sx={{ paddingY: 0, width: "70%", marginTop: 2 }}
            severity="error"
          >
            {getRequestErrorMessage(loadDatasetError, "No dataset found", {
              retryAction: "loading this Hugging Face dataset",
            })}
          </Alert>
        )}
        {isSuccess && (
          <form
            style={{
              width: "500px",
              marginTop: "16px",
            }}
          >
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
              <Typography
                fontWeight={700}
                fontSize="14px"
                color="text.secondary"
              >
                Dataset Details
              </Typography>
              <HelperText text="Only the first 100 rows of this dataset will be ingested" />
            </Box>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: "22px",
                alignItems: "center",
                paddingTop: 0.5,
              }}
            >
              <FormTextFieldV2
                size="small"
                fieldName="new_dataset_name"
                control={control}
                label="Name"
                placeholder="Enter dataset name"
                fullWidth
                rules={{ required: "Name is required" }}
              />
              {/* <FormSelectField
                size="small"
                label="Model type"
                control={control}
                fieldName="model_type"
                fullWidth
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
              <FormSelectField
                size="small"
                label="Subset"
                control={control}
                fieldName="huggingface_dataset_config"
                fullWidth
                options={subsetOptions}
                MenuProps={{
                  PaperProps: {
                    sx: {
                      maxHeight: 224,
                    },
                  },
                }}
              />
              <FormSelectField
                size="small"
                label="Split"
                control={control}
                fieldName="huggingface_dataset_split"
                fullWidth
                options={splitOptions}
                MenuProps={{
                  PaperProps: {
                    sx: {
                      maxHeight: 224,
                    },
                  },
                }}
                disabled={!subset?.length}
              />
              <LoadingButton
                variant="contained"
                color="primary"
                type="submit"
                sx={{ width: "40%" }}
                loading={isLoadingCreateDataset}
                onClick={handleSubmit(onSubmit)}
              >
                Start Experimenting
              </LoadingButton>
            </Box>
          </form>
        )}
      </Box>
    </Dialog>
  );
};

ImportFromHuggingFace.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default ImportFromHuggingFace;
