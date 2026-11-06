import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Divider, IconButton, Typography, Tooltip } from "@mui/material";
import { useFormContext, Controller } from "react-hook-form";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

export default function ModelSelectionRow({
  modelConfig,
  isModelSelected,
  onModelChange,
  onParamsClick,
  disabled,
}) {
  const { control, formState } = useFormContext();
  const [openSelectModel, setOpenSelectModel] = useState(false);
  const modelContainerRef = useRef(null);
  const modelError = formState.errors?.modelConfig?.model;

  return (
    <Box display="flex" flexDirection="column" sx={{ width: "100%" }}>
      <Box display="flex" sx={{ alignItems: "center" }}>
        <Box
          display="flex"
          flexDirection="row"
          borderRadius="4px"
          gap={1}
          ref={modelContainerRef}
          sx={{
            border: "1px solid",
            borderColor: modelError ? "error.main" : "divider",
            height: "32px",
            display: "flex",
            alignItems: "center",
            width: "100%",
            borderRadius: (theme) =>
              openSelectModel
                ? theme.spacing(0.5, 0.5, 0, 0)
                : theme.spacing(0.5),
          }}
        >
          <Controller
            name="modelConfig"
            control={control}
            render={({ field }) => {
              const currentModel = field.value?.model || modelConfig?.model;
              const currentModelDetail =
                field.value?.modelDetail || modelConfig?.modelDetail;

              return (
                <CustomModelDropdown
                  isModalContainer
                  openSelectModel={openSelectModel}
                  setOpenSelectModel={setOpenSelectModel}
                  hoverPlacement="bottom-start"
                  buttonTitle="Select Model"
                  buttonIcon={
                    <Iconify
                      icon="solar:graph-linear"
                      width="16px"
                      height="16px"
                      sx={{ cursor: "pointer", color: "text.primary" }}
                    />
                  }
                  value={currentModel}
                  modelDetail={currentModelDetail}
                  onChange={(e) => {
                    const value = e.target.value;
                    // Update both model and modelDetail in the form
                    field.onChange({
                      ...field.value,
                      model: value.model_name,
                      modelDetail: value,
                      maxTokens: 1000,
                    });
                    // Call the original onChange handler if provided
                    onModelChange?.(e);
                  }}
                  modelContainerRef={modelContainerRef}
                  extraParams={{ model_type: "llm" }}
                />
              );
            }}
          />
          <Divider
            orientation="vertical"
            flexItem
            sx={{
              height: "20px",
              borderColor: "divider",
              marginBottom: 1,
              marginTop: "4px",
            }}
          />
          <Tooltip
            title={!isModelSelected ? "Select model first" : ""}
            placement="top"
            arrow
            componentsProps={{
              tooltip: {
                sx: {},
              },
              arrow: {
                sx: {},
              },
            }}
          >
            <span>
              <IconButton
                size="small"
                sx={{
                  pr: 1,
                  opacity: isModelSelected ? 1 : 0.5,
                  "&:disabled": {
                    cursor: "not-allowed",
                    pointerEvents: "auto",
                  },
                }}
                disabled={!isModelSelected || disabled}
                onClick={onParamsClick}
              >
                <SvgColor
                  src="/assets/prompt/slider-options.svg"
                  sx={{
                    height: "16px",
                    width: "16px",
                    bgcolor: "text.primary",
                  }}
                />
              </IconButton>
            </span>
          </Tooltip>
        </Box>
      </Box>
      {modelError && (
        <Typography
          variant="caption"
          sx={{
            color: "error.main",
            mt: 0.5,
            ml: 1,
          }}
        >
          {modelError.message}
        </Typography>
      )}
    </Box>
  );
}

ModelSelectionRow.propTypes = {
  modelConfig: PropTypes.shape({
    model: PropTypes.string,
    modelDetail: PropTypes.object,
  }),
  isModelSelected: PropTypes.bool.isRequired,
  onModelChange: PropTypes.func.isRequired,
  onParamsClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
