import { Box, Button, IconButton, Typography } from "@mui/material";
import { useSnackbar } from "src/components/snackbar";
import React from "react";
import { RHFUpload } from "src/components/hook-form";
import { formatFileSize } from "src/utils/utils";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";

const UploadScriptOption = ({ control }) => {
  const fieldName = "config.scriptUrl";
  const { enqueueSnackbar } = useSnackbar();
  const { field } = useController({
    name: fieldName,
    control,
  });

  const scriptUrl = field?.value;

  const handleFileChange = (acceptedFiles, fileRejections = []) => {
    if (fileRejections.length > 0) {
      const hasTypeError = fileRejections.some((rejection) =>
        rejection.errors?.some((err) => err.code === "file-invalid-type"),
      );
      enqueueSnackbar(
        hasTypeError
          ? "Unsupported file type. Please upload a TXT or PDF file."
          : "File could not be uploaded",
        { variant: "error" },
      );
      return;
    }

    const files = Array.from(acceptedFiles);
    const maxSize = 5 * 1024 * 1024; // 5MB

    const filesLargerThanMaxSize = files.filter((file) => file?.size > maxSize);

    if (filesLargerThanMaxSize.length > 0) {
      enqueueSnackbar("File size is too large", {
        variant: "error",
      });
      return;
    }

    const validFiles = files.filter((file) => file.size <= maxSize);

    // Process each file to create preview URLs and metadata
    const processedFiles = validFiles.map((file) => {
      //   const fileId = getRandomId();
      //   const previewUrl = URL.createObjectURL(file);

      return {
        file: file,
        name: file.name,
        size: file.size,
      };
    });

    if (field?.onChange) {
      field.onChange(processedFiles?.[0]);
    }
  };
  return (
    <Box>
      <ShowComponent condition={scriptUrl}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            padding: 2,
            backgroundColor: "background.paper",
            borderRadius: "4px",
            justifyContent: "space-between",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              width: "100%",
            }}
          >
            <SvgColor
              src="/assets/icons/components/ic_script.svg"
              sx={{ width: "24px", height: "24px", color: "primary.main" }}
            />
            <Box>
              <Typography
                typography="s1"
                color="text.primary"
                fontWeight="fontWeightMedium"
              >
                {scriptUrl?.name}
              </Typography>
              <Typography typography="s2" color="text.primary">
                {formatFileSize(scriptUrl?.size)}
              </Typography>
            </Box>
          </Box>

          <IconButton
            size="small"
            onClick={() => {
              field.onChange(null);
            }}
          >
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{ width: "16px", height: "16px", color: "text.primary" }}
            />
          </IconButton>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!scriptUrl}>
        <RHFUpload
          control={control}
          showDropRejection={false}
          name="fieldName"
          hidePreview={true}
          uploadIcon={
            <SvgColor
              src="/assets/icons/components/ic_script.svg"
              sx={{ width: "32px", height: "32px", color: "primary.main" }}
            />
          }
          heading="Upload Script"
          description="Upload AI agent scripts for testing scenarios (TEXT/PDF)"
          actionButton={
            <Button size="small" variant="outlined" color="primary">
              Browse Files
            </Button>
          }
          showIllustration={false}
          accept={{
            "text/plain": [".txt"],
            "application/pdf": [".pdf"],
          }}
          sx={{ paddingY: (theme) => theme.spacing(3) }}
          onDrop={handleFileChange}
        />
      </ShowComponent>
    </Box>
  );
};

UploadScriptOption.propTypes = {
  control: PropTypes.object,
};

export default UploadScriptOption;
