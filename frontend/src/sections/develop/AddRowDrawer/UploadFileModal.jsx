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
import { useController, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import UploadedFileIllustration from "./UploadFileIllustration";
import { zodResolver } from "@hookform/resolvers/zod";
import { UploadFileValidationSchema } from "./validation";
import { RHFUpload } from "src/components/hook-form";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import UploadCard from "../AddDatasetDrawer/UploadCard";

const defaultValues = {
  name: "",
  file: null,
  modelType: "",
};

const UploadFileModal = ({
  open,
  onClose,
  refreshGrid,
  datasetId,
  closeDrawer,
}) => {
  const { control, handleSubmit, reset, setValue } = useForm({
    defaultValues,
    resolver: zodResolver(UploadFileValidationSchema),
  });

  const queryClient = useQueryClient();

  const { mutate: uploadDataset, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.uploadDatasetRow, data, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: () => {
      trackEvent(Events.addRowsSuccess, {
        [PropertyName.method]: "add using Upload a file",
      });
      enqueueSnackbar("Dataset uploaded successfully", {
        variant: "success",
      });
      // Invalidate cached dataset data to force fresh fetch after file upload
      queryClient.invalidateQueries({
        queryKey: ["dataset-detail", datasetId],
      });
      // Invalidate JSON schema query to refresh maxImagesCount for images columns
      queryClient.invalidateQueries({
        queryKey: ["json-column-schema", datasetId],
      });
      refreshGrid(null, true);
      onClose();
      reset();
      closeDrawer();
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
  };

  const onSubmit = (data) => {
    const formData = new FormData();
    formData.append("dataset_id", datasetId);
    formData.append("file", data.file.file);
    uploadDataset(formData);
  };

  const onCloseClick = () => {
    onClose();
  };

  const onDelete = () => {
    setValue("file", null);
  };

  return (
    <Dialog open={open} onClose={onCloseClick} maxWidth="sm">
      <Box padding={2}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <Typography
                variant="m3"
                fontWeight={"fontWeightBold"}
                color="text.primary"
              >
                Upload a File
              </Typography>
              <IconButton onClick={onCloseClick}>
                <Iconify icon="mdi:close" color="text.primary" />
              </IconButton>
            </Box>
            <HelperText
              text="Uploading a JSONL/JSON/CSV file to add datapoints to your dataset. For
              sample JSONL/JSON/CSV file and format details"
            />
          </Box>
        </DialogTitle>

        <DialogContent sx={{ gap: 2, p: 0, m: "16px 0 0" }}>
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
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
              }}
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
        </DialogContent>
        <DialogActions sx={{ padding: 0, margin: "32px 0 0" }}>
          <Button size="small" onClick={onCloseClick} variant="outlined">
            Cancel
          </Button>
          <LoadingButton
            onClick={handleSubmit(onSubmit)}
            variant="contained"
            autoFocus
            size="small"
            color="primary"
            loading={isPending}
          >
            Done
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

UploadFileModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  closeDrawer: PropTypes.func,
};

export default UploadFileModal;
