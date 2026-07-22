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
import React, { useState } from "react";
import { useController, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import UploadedFileIllustration from "./UploadedFileIllustration";
import { zodResolver } from "@hookform/resolvers/zod";
import { UploadFileValidationSchema } from "./validation";
import { RHFUpload } from "src/components/hook-form";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import { useNavigate } from "react-router";
import UploadCard from "./UploadCard";
import { ConfirmDialog } from "src/components/custom-dialog";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { getRequestErrorMessage } from "src/utils/errorUtils";

const defaultValues = {
  name: "",
  file: null,
  modelType: "GenerativeLLM",
};

const UploadFileModal = ({ open, onClose, refreshGrid }) => {
  const {
    control,
    handleSubmit,
    reset,
    setValue,
    getValues,
    formState: { isDirty },
  } = useForm({
    defaultValues,
    resolver: zodResolver(UploadFileValidationSchema),
  });

  const queryClient = useQueryClient();
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const navigate = useNavigate();

  const { mutate: uploadDataset, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.uploadDatasetLocalFile, data, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: (data) => {
      enqueueSnackbar("Dataset uploaded successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      queryClient.invalidateQueries({ queryKey: ["dataset-detail"] });
      trackEvent(Events.datasetFromJSONCSVSuccessful, {
        [PropertyName.datasetId]: data?.data?.result?.dataset_id,
      });
      onCloseClick(null, true);
      reset();
      refreshGrid();
      navigate(`/dashboard/develop/${data?.data?.result?.dataset_id}?tab=data`);
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to upload dataset", {
          retryAction: "uploading this dataset file",
        }),
        { variant: "error" },
      );
    },
  });

  const { field } = useController({
    name: "file",
    control,
  });

  const handleFileChange = (acceptedFiles) => {
    const file = acceptedFiles[0];
    field.onChange({
      file,
      preview: <UploadedFileIllustration width={100} height={100} />,
    });

    // Auto-populate dataset name from filename if name is empty
    const currentName = getValues("name");
    if (!currentName && file?.name) {
      const nameWithoutExtension = file.name.replace(/\.[^/.]+$/, "");
      const cleanedName = nameWithoutExtension
        .replace(/[^a-zA-Z0-9\s-_]/g, "")
        .replace(/\s+/g, " ")
        .trim();
      if (cleanedName) {
        // Check for duplicate names in existing datasets
        const cachedData = queryClient.getQueryData([
          "develop",
          "dataset-name-list",
        ]);
        const existingNames = (cachedData?.data?.result?.datasets ?? []).map(
          (d) => d.name?.toLowerCase(),
        );

        let finalName = cleanedName;
        if (existingNames.includes(finalName.toLowerCase())) {
          let version = 2;
          while (
            existingNames.includes(`${cleanedName} v${version}`.toLowerCase())
          ) {
            version++;
          }
          finalName = `${cleanedName} v${version}`;
        }

        setValue("name", finalName, {
          shouldDirty: true,
          shouldValidate: true,
        });
      }
    }
  };

  const onSubmit = (data) => {
    const formData = new FormData();
    formData.append("new_dataset_name", data.name);
    formData.append("model_type", data.modelType);
    formData.append("file", data.file.file);
    //@ts-ignore
    uploadDataset(formData);
  };

  const onCloseClick = (_, skipConfirmation = false) => {
    if (!skipConfirmation && isDirty) {
      setIsDialogOpen(true);
    } else {
      reset();
      onClose();
    }
  };

  const handleDialogClose = (confirm) => {
    if (confirm) {
      reset();
      onClose();
    }
    setIsDialogOpen(false);
  };

  const onDelete = () => {
    setValue("file", null);
  };

  return (
    <Dialog
      open={open}
      onClose={() => onCloseClick()}
      maxWidth="sm"
      fullWidth
      aria-labelledby="upload-file-dialog-title"
      aria-describedby="upload-file-dialog-description"
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle
          sx={{
            gap: "2px",
            display: "flex",
            flexDirection: "column",
            padding: 0,
            margin: 0,
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
              id="upload-file-dialog-title"
              fontWeight={"fontWeightBold"}
              color="text.primary"
              variant="h5"
              sx={{ fontSize: "20px" }}
            >
              Upload a File
            </Typography>
            <IconButton onClick={() => onCloseClick()}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText
            id="upload-file-dialog-description"
            text="Uploading a JSONL/JSON/CSV file to add datapoints to your dataset. For sample JSONL/JSON/CSV file and format details"
          />
        </DialogTitle>

        <DialogContent sx={{ padding: 0 }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              marginTop: 2,
            }}
          >
            <FormTextFieldV2
              fieldName="name"
              control={control}
              label="Name"
              size="small"
              required
              placeholder="Dataset Name"
              fullWidth
              rules={{ required: "Name is required" }}
            />

            {!field?.value ? (
              <RHFUpload
                showIcon={true}
                control={control}
                name="file"
                multiple={false}
                showIllustration={false}
                accept={{
                  "text/plain": [".jsonl"],
                  "application/json": [".json"],
                  "text/csv": [".csv"],
                }}
                onDrop={handleFileChange}
                heading="Choose a file or drag & drop it here"
                description="Supports JSONL, JSON, and CSV file format up to 25 MB"
                actionButton={
                  <Button
                    variant="outlined"
                    size="small"
                    sx={{
                      paddingY: (theme) => theme.spacing(0.75),
                      paddingX: (theme) => theme.spacing(3),
                      borderRadius: (theme) => theme.spacing(1),
                      background: (theme) => theme.palette.divider,
                      color: "text.primary",
                      borderColor: "text.disabled",
                    }}
                  >
                    Browse files
                  </Button>
                }
              />
            ) : (
              <UploadCard fileData={field?.value?.file} onDelete={onDelete} />
            )}
          </Box>
        </DialogContent>

        <DialogActions sx={{ padding: 0, marginTop: 4 }}>
          <Button fullWidth onClick={() => onCloseClick()} variant="outlined">
            {/* <Typography
              variant="s2"
              width={"80px"}
              fontWeight={"fontWeightSemiBold"}
              color="text.secondary"
            > */}
            Cancel
            {/* </Typography> */}
          </Button>
          <LoadingButton
            onClick={handleSubmit(onSubmit)}
            variant="contained"
            fullWidth
            autoFocus
            color="primary"
            loading={isPending}
            disabled={!isDirty}
          >
            {/* <Typography
              variant="s2"
              width={"80px"}
              fontWeight={"fontWeightSemiBold"}
            > */}
            Save
            {/* </Typography> */}
          </LoadingButton>
        </DialogActions>
      </Box>

      <ConfirmDialog
        open={isDialogOpen}
        title="Confirm Action"
        message="Are you sure you want to close?"
        content="Your unsaved changes will be lost. Do you still want to close?"
        onClose={() => handleDialogClose(false)}
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => handleDialogClose(true)}
          >
            Confirm
          </Button>
        }
      />
    </Dialog>
  );
};

UploadFileModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default UploadFileModal;
