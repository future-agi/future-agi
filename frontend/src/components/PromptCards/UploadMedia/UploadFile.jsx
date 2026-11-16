import { Box, Button } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { RHFUpload } from "src/components/hook-form";
import Iconify from "src/components/iconify";
import { useController } from "react-hook-form";
import { useSnackbar } from "src/components/snackbar";
import { getRandomId } from "src/utils/utils";

const DescriptionMap = {
  image: [
    "Add images up to 5 MB each (1 GB total storage).",
    "File formats supported: JPG, PNG, GIF, WebP",
  ],
  audio: [
    "Add audio up to 5 MB each (1 GB total storage).",
    "File formats supported: MP3, WAV, OGG, M4A",
  ],
  pdf: [
    "Add pdf up to 5 MB each (1 GB total storage).",
    "File formats supported: PDF",
  ],
};

const AcceptMap = {
  image: {
    "image/jpeg": [".jpg", ".jpeg"],
    "image/png": [".png"],
    "image/gif": [".gif"],
    "image/webp": [".webp"],
  },
  audio: {
    "audio/mpeg": [".mp3", ".mpeg"],
    "audio/wav": [".wav"],
    "audio/ogg": [".ogg"],
    "audio/x-m4a": [".m4a"],
  },
  pdf: {
    "application/pdf": [".pdf"],
  },
};

const UploadFile = ({ control, type }) => {
  const { field } = useController({
    name: "files",
    control,
  });

  const { enqueueSnackbar } = useSnackbar();

  const handleFileChange = (acceptedFiles) => {
    const files = Array.from(acceptedFiles);
    const maxSize = 5 * 1024 * 1024; // 5MB

    const filesLargerThanMaxSize = files.filter((file) => file?.size > maxSize);

    if (filesLargerThanMaxSize.length > 0) {
      enqueueSnackbar("File size is too large", {
        variant: "error",
      });
      return;
    }

    const existingFiles = field?.value || [];
    const validFiles = files.filter((file) => file.size <= maxSize);

    // Process each file to create preview URLs and metadata
    const processedFiles = validFiles.map((file) => {
      const fileId = getRandomId();
      const previewUrl = URL.createObjectURL(file);

      return {
        id: fileId,
        file: file,
        name: file.name,
        size: file.size,
        previewUrl: previewUrl,
        type: file.type,
      };
    });

    const updatedFiles = [...existingFiles, ...processedFiles];

    if (field?.onChange) {
      field.onChange(updatedFiles);
    }
  };

  // Cleanup preview URLs when component unmounts
  React.useEffect(() => {
    return () => {
      const files = field?.value || [];
      files.forEach((file) => {
        if (file.previewUrl) {
          URL.revokeObjectURL(file.previewUrl);
        }
      });
    };
  }, [field?.value]);

  return (
    <Box>
      <RHFUpload
        control={control}
        showDropRejection={false}
        name="files"
        hidePreview={true}
        uploadIcon={
          <Iconify
            icon="solar:download-minimalistic-bold"
            height={24}
            width={24}
            color="primary.main"
          />
        }
        heading="Choose a file or drag & drop it here"
        description={DescriptionMap[type]}
        actionButton={
          <Button
            variant="outlined"
            size="small"
            sx={{
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
        accept={AcceptMap[type]}
        sx={{ paddingY: (theme) => theme.spacing(3) }}
        onDrop={handleFileChange}
      />
    </Box>
  );
};

UploadFile.propTypes = {
  control: PropTypes.object.isRequired,
  type: PropTypes.string.isRequired,
};

export default UploadFile;
