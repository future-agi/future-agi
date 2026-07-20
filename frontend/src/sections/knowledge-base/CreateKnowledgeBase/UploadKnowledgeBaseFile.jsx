import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { RHFUpload } from "src/components/hook-form";
import Iconify from "src/components/iconify";
import { useController } from "react-hook-form";

const UploadKnowledgeBaseFile = ({ control, handleShowSdkInfo, isPending }) => {
  const { field } = useController({
    name: "file",
    control,
  });

  const handleFileChange = (acceptedFiles, rejected = []) => {
    // const file = acceptedFiles;
    const files = Array.from(acceptedFiles);
    const maxSize = 5 * 1024 * 1024; // 5MB

    const filesLargerThanMaxSize = files.filter((file) => file?.size > maxSize);

    if (filesLargerThanMaxSize.length > 0) {
      handleShowSdkInfo();
    }

    const existingFiles = field?.value?.file || [];
    // const fileLists = files.map(file => allowedTypes.includes(file.type) ? )

    const validFiles = files.filter((file) => file.size <= maxSize);

    const updatedFiles = [
      ...existingFiles,
      ...validFiles.map((file) => ({ item: file, status: "not_started" })),
      ...rejected.map((item) => {
        const { file, errors } = item;
        return {
          item: file,
          status: "error",
          statusReason: errors?.[0]?.message,
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
          "File formats supported: PDF, DOCX, RTF, TXT, images (PNG, JPG, JPEG, WEBP, GIF, SVG, BMP)",
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
          // "application/msword": [".doc"],
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            [".docx"],
          "text/plain": [".txt"],
          "text/rtf": [".rtf"],
          "image/*": [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"],
        }}
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
