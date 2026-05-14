import React from "react";
import PropTypes from "prop-types";
import { Stack } from "@mui/material";
import CustomModelTools from "src/components/custom-model-tools";
import CustomTooltip from "src/components/tooltip";
import ResponseFormatDropdown from "./ResponseFormatDropdown";
import TemplateFormatSelector from "src/sections/workbench/createPrompt/Playground/TemplateFormatSelector";

export default function OutputToolsRow({
  control,
  isModelSelected,
  responseFormatMenuItems,
  onCreateSchema,
  modelConfig,
  onToolsApply,
  disabled,
  templateFormat = "mustache",
  onTemplateFormatChange,
}) {
  const tooltipProps = {
    show: !isModelSelected,
    title: "Select a model first",
    placement: "bottom",
    arrow: true,
    size: "small",
  };

  return (
    <Stack direction="row" gap={1} alignItems="center">
      <CustomTooltip {...tooltipProps}>
        <span>
          <ResponseFormatDropdown
            control={control}
            fieldName="modelConfig.responseFormat"
            options={responseFormatMenuItems}
            disabled={!isModelSelected || disabled}
            onCreateSchema={onCreateSchema}
          />
        </span>
      </CustomTooltip>
      <CustomTooltip {...tooltipProps}>
        <span>
          <CustomModelTools
            isModalContainer
            handleApply={onToolsApply}
            tools={modelConfig?.tools || []}
            disableClick={!isModelSelected || disabled}
            disableHover={
              !modelConfig?.tools ||
              modelConfig?.tools.length === 0 ||
              !isModelSelected ||
              disabled
            }
            label="Tools"
          />
        </span>
      </CustomTooltip>
      <TemplateFormatSelector
        value={templateFormat}
        onChange={onTemplateFormatChange}
        disabled={disabled}
      />
    </Stack>
  );
}

OutputToolsRow.propTypes = {
  control: PropTypes.any.isRequired,
  isModelSelected: PropTypes.bool.isRequired,
  responseFormatMenuItems: PropTypes.array.isRequired,
  onCreateSchema: PropTypes.func.isRequired,
  modelConfig: PropTypes.object,
  onToolsApply: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  templateFormat: PropTypes.string,
  onTemplateFormatChange: PropTypes.func,
};
