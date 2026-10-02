import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { RHFUpload } from "src/components/hook-form";
import Iconify from "src/components/iconify";
import { useController } from "react-hook-form";
import { useSnackbar } from "src/components/snackbar";

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

const UploadKnowledgeBaseFile = ({ control, handleShowSdkInfo, isPending }) => {
  const { enqueueSnackbar } = useSnackbar();
  const { field } = useController({
    name: "file",
    control,
  });

  const handleFileChange = (acceptedFiles, rejected = []) => {
    const files = Array.from(acceptedFiles || []);

    const safeRejected = Array.isArray(rejected) ? rejected : [];

    // Show SDK info dialog if any file is too large
    if (
      safeRejected.some((r) =>
        (r?.errors || []).some((e) => e?.code === "file-too-large"),
      )
    ) {
      handleShowSdkInfo();
    }

    // Report rejected files via snackbar (do not add them to the file list —
    // oversized rows would block the Create button for other valid files)
    safeRejected.forEach((item) => {
      const { file, errors } = item || {};

      // Skip rejections with no file to prevent downstream crashes
      // (CreateKnowledgeBaseDrawer reads file.item.size / file.item.type without guards)
      if (!file) return;

      const safeErrors = errors || [];
      const isTooSmall = safeErrors.some((e) => e?.code === "file-too-small");
      const isInvalidType = safeErrors.some(
        (e) => e?.code === "file-invalid-type",
      );
      const isTooLarge = safeErrors.some((e) => e?.code === "file-too-large");

      if (isTooSmall) {
        enqueueSnackbar(
          `"${file.name}" is empty. Please upload a file with content.`,
          { variant: "error" },
        );
      } else if (isInvalidType) {
        enqueueSnackbar(
          "Unsupported file type. Please upload a PDF, DOCX, RTF, or TXT file.",
          { variant: "error" },
        );
      } else if (isTooLarge) {
        enqueueSnackbar(
          "File size is too large. Please upload a file under 5 MB.",
          { variant: "error" },
        );
      } else {
        enqueueSnackbar(
          `"${file.name}" could not be uploaded. ${safeErrors[0]?.message || "File was rejected"}`,
          { variant: "error" },
        );
      }
    });

    const existingFiles = field?.value?.file || [];

    // Only add accepted files — rejected files are reported via toasts / SDK dialog
    const updatedFiles = [
      ...existingFiles,
      ...files.map((file) => ({ item: file, status: "not_started" })),
    ];

    if (field?.onChange) {
      field.onChange({ file: updatedFiles });
    }
  };

  return (
    <Box>
      <RHFUpload
        disabled={isPending}
        control={control}
        showDropRejection={false}
        name="file"
        uploadIcon={
          <Iconify
            icon="solar:download-minimalistic-bold"
            height={24}
            width={24}
            color="primary.main"
          />
        }
        heading="Choose a file or drag & drop it here"
        description={[
          "Add documents up to 5 MB each (1 GB total storage).",
          "File formats supported: PDF, DOCX, RTF, TXT",
        ]}
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
        multiple={true}
        showIllustration={false}
        accept={{
          "application/pdf": [".pdf"],
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            [".docx"],
          "text/plain": [".txt"],
          "text/rtf": [".rtf"],
        }}
        maxSize={MAX_FILE_SIZE}
        minSize={1}
        sx={{ paddingY: 3 }}
        onDrop={handleFileChange}
      />
    </Box>
  );
};

export default UploadKnowledgeBaseFile;

UploadKnowledgeBaseFile.propTypes = {
  control: PropTypes.any,
  handleShowSdkInfo: PropTypes.func,
  isPending: PropTypes.bool,
};
