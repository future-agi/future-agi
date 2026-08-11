import { Box, Button, IconButton, Typography } from "@mui/material";
import { useSnackbar } from "src/components/snackbar";
import React from "react";
import { RHFUpload } from "src/components/hook-form";
import { formatFileSize } from "src/utils/utils";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { createScenarioFileDropHandler } from "./common";

const UploadScriptOption = ({ control }) => {
  const fieldName = "config.scriptUrl";
  const { enqueueSnackbar } = useSnackbar();
  const { field } = useController({
    name: fieldName,
    control,
  });

  const scriptUrl = field?.value;

  const handleFileChange = createScenarioFileDropHandler({
    enqueueSnackbar,
    onChange: field?.onChange,
  });
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
