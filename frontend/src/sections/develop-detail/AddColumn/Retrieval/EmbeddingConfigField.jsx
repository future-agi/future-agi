import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useMemo } from "react";
import { useApiKeysStatus } from "src/api/model/api-keys";
import ConfigureKeys from "../../Common/ConfigureKeys/ConfigureKeys";
import _ from "lodash";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const EmbeddingConfigField = ({ control }) => {
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);

  const { data } = useApiKeysStatus({});

  const EmbeddingType = useMemo(() => {
    const defaultOptions = [
      {
        label: "Open AI",
        value: "openai",
        disabled: false,
      },
      {
        label: "Hugging Face",
        value: "huggingface",
        disabled: false,
      },
      {
        label: "Cohere",
        value: "cohere",
        disabled: false,
      },
      {
        label: "Gemini",
        value: "gemini",
        disabled: false,
      },
      {
        label: "Vertex AI",
        value: "vertex_ai",
        disabled: false,
      },
      {
        label: "Voyage",
        value: "voyage",
        disabled: false,
      },
      {
        label: "Mistral",
        value: "mistral",
        disabled: false,
      },
    ];

    defaultOptions.forEach((item, index) => {
      if (data?.find((d) => d.provider === item.value && d.hasKey === false)) {
        defaultOptions[index].disabled = true;
      }
    });

    defaultOptions.push({
      label: "Sentence Transformers",
      value: "sentence_transformers",
      disabled: false,
    });

    return defaultOptions;
  }, [data]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Typography fontSize="12px" fontWeight={500} color="text.secondary">
        Embedding Configuration
      </Typography>
      <Box sx={{ display: "flex", gap: 1 }}>
        <FormSearchSelectFieldControl
          control={control}
          fieldName="embeddingConfig.type"
          size="small"
          label="Type"
          fullWidth
          options={EmbeddingType.map((option) => ({
            value: option.value,
            label: option.label,
            disabled: option.disabled,
          }))}
        />
        <ConfigureKeys
          open={isApiConfigurationOpen}
          onClose={() => setApiConfigurationOpen(false)}
        />
        <FormTextFieldV2
          label="Model"
          size="small"
          placeholder="Enter model"
          control={control}
          fieldName="embeddingConfig.model"
          fullWidth
        />
      </Box>
    </Box>
  );
};

EmbeddingConfigField.propTypes = {
  control: PropTypes.object,
};

export default EmbeddingConfigField;
