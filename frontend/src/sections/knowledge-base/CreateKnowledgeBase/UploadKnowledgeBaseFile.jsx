import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { RHFUpload } from "src/components/hook-form";
import Iconify from "src/components/iconify";
import { useController } from "react-hook-form";

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

const UploadKnowledgeBaseFile = ({ control, handleShowSdkInfo, isPending }) => {
  const { field } = useController({
    name: "file",
    control,
  });

  const handleFileChange = (acceptedFiles, rejected = []) => {
    const files = Array.from(acceptedFiles || []);

    const safeRejected = Array.isArray(rejected) ? rejected : [];
    if (safeRejected.some((r) => r?.errors?.some?.((e) => e?.code === "file-too-large"))) {
      handleShowSdkInfo();
    }

    const existingFiles = field?.value?.file || [];

    const updatedFiles = [
      ...existingFiles,
      ...files.map((file) => ({ item: file, status: "not_started" })),
      ...safeRejected.map((item) => {
        const { file, errors = [] } = item || {};
        return {
          item: file,
          status: "error",
          statusReason: errors?.[0]?.message || "File was rejected",
        };
      }),
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
