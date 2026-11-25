import { Box, Typography } from "@mui/material";
import React from "react";
import SliderRow from "../../common/SliderRow/SliderRow";
import Iconify from "src/components/iconify";
import { FormSelectField } from "src/components/FormSelectField";
import PropTypes from "prop-types";

const ModelOptions = ({ control }) => {
  return (
    <Box sx={{ display: "flex", gap: 2, flexDirection: "column" }}>
      <Typography color="text.secondary" fontSize="12px" fontWeight={600}>
        Model Options
      </Typography>
      <SliderRow
        label="Temperature"
        control={control}
        fieldName="modelConfig.temperature"
        min={0}
        max={1}
        step={0.01}
      />
      <SliderRow
        label="Top P"
        control={control}
        fieldName="modelConfig.topP"
        min={0}
        max={1}
        step={0.01}
      />
      <SliderRow
        label="Max Tokens"
        control={control}
        fieldName="modelConfig.maxTokens"
        min={1}
        max={20000}
        step={1}
      />
      <SliderRow
        label="Presence Penalty"
        control={control}
        fieldName="modelConfig.presencePenalty"
        min={-2}
        max={2}
        step={0.01}
      />
      <SliderRow
        label="Frequency Penalty"
        control={control}
        fieldName="modelConfig.frequencyPenalty"
        min={-2}
        max={2}
        step={0.01}
      />
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography variant="body2">Response Format</Typography>
          <Iconify icon="solar:info-circle-bold" color="text.disabled" />
        </Box>
        <FormSelectField
          control={control}
          fieldName="modelConfig.responseFormat"
          size="small"
          sx={{ width: 200 }}
          options={[
            { value: "text", label: "Text" },
            { value: "json", label: "JSON" },
          ]}
        />
      </Box>
    </Box>
  );
};

ModelOptions.propTypes = {
  control: PropTypes.object,
};

export default ModelOptions;
