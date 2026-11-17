import React from "react";
import PropTypes from "prop-types";
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
import { LoadingButton } from "@mui/lab";
import { useController, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import { RHFUpload } from "src/components/hook-form";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { PROPOSED_CLASSIC_ROW_UPLOAD_COPY as COPY } from "../importAvailabilityCopy";
import UploadCard from "../AddDatasetDrawer/UploadCard";
import UploadedFileIllustration from "./UploadFileIllustration";
import { UploadFileValidationSchema } from "./validation";

/**
 * v1.56.1 file append: send the original file, not IM1 mapping/operations.
 * Kept separate so an unclaimed imports/versions service is never mounted,
 * queried or offered as a role/transform control in a classic-write build.
 */
export default function ClassicRowUploadModal({
  open,
  onClose,
  refreshGrid,
  datasetId,
  closeDrawer,
}) {
  const { control, handleSubmit, reset, setValue } = useForm({
    defaultValues: { file: null },
    resolver: zodResolver(UploadFileValidationSchema),
  });
  const { field } = useController({ name: "file", control });
  const queryClient = useQueryClient();
  const { mutate: uploadRows, isPending } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.uploadDatasetRow, body, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: () => {
      trackEvent(Events.addRowsSuccess, {
        [PropertyName.method]: "add using Upload a file",
      });
      enqueueSnackbar(COPY.success, { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["dataset-detail", datasetId],
      });
      queryClient.invalidateQueries({
        queryKey: ["json-column-schema", datasetId],
      });
      refreshGrid?.(null, true);
      onClose();
      reset();
      closeDrawer?.();
    },
    onError: (error) =>
      enqueueSnackbar(
        typeof error?.result === "string"
          ? error.result
          : error?.message ?? COPY.error,
        { variant: "error" },
      ),
  });
  const submit = (data) => {
    const body = new FormData();
    body.append("dataset_id", datasetId);
    body.append("file", data.file.file);
    uploadRows(body);
  };
  const selectFile = (acceptedFiles) => {
    const file = acceptedFiles[0];
    if (!file) return;
    field.onChange({
      file,
      preview: <UploadedFileIllustration width={100} height={100} />,
    });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <Box padding={2}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              variant="m3"
              fontWeight="fontWeightBold"
              color="text.primary"
            >
              {COPY.title}
            </Typography>
            <IconButton onClick={onClose} aria-label={COPY.close}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <Typography typography="s2" color="text.secondary">
            {COPY.subtitle}
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ gap: 2, p: 0, m: "16px 0 0" }}>
          {!field.value ? (
            <RHFUpload
              showIcon
              control={control}
              name="file"
              multiple={false}
              showIllustration={false}
              accept={{
                "text/plain": [".jsonl"],
                "application/json": [".json"],
                "text/csv": [".csv"],
              }}
              onDrop={selectFile}
              heading={COPY.heading}
              description={COPY.formats}
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
              }}
              actionButton={
                <Button variant="outlined" size="small">
                  {COPY.browse}
                </Button>
              }
            />
          ) : (
            <UploadCard
              fileData={field.value.file}
              onDelete={() => setValue("file", null)}
            />
          )}
        </DialogContent>
        <DialogActions sx={{ padding: 0, margin: "32px 0 0" }}>
          <Button size="small" onClick={onClose} variant="outlined">
            {COPY.cancel}
          </Button>
          <LoadingButton
            onClick={handleSubmit(submit)}
            variant="contained"
            size="small"
            color="primary"
            loading={isPending}
            disabled={!field.value}
          >
            {COPY.done}
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
}

ClassicRowUploadModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  closeDrawer: PropTypes.func,
};
