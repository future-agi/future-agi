import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useMemo } from "react";
import { useWatch } from "react-hook-form";
import { useApiKeysStatus } from "src/api/model/api-keys";
import ConfigureKeys from "../../Common/ConfigureKeys/ConfigureKeys";
import _ from "lodash";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { APPROVED_HUGGINGFACE_EMBEDDING_MODELS } from "./validation";

// What model serving loads for each Hugging Face-backed type, shown before
// submit (validation.js refuses any other ID with the server's message).
const MODEL_HINTS = {
  huggingface: `Approved models: ${APPROVED_HUGGINGFACE_EMBEDDING_MODELS.join(", ")}`,
  // Model serving has no sentence_transformers model (it answers 404).
  sentence_transformers:
    "Sentence Transformers embeddings are not available on this server. Use Hugging Face instead.",
};

const EmbeddingConfigField = ({ control }) => {
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);

  const { data } = useApiKeysStatus({});
  const embeddingType = useWatch({ control, name: "embeddingConfig.type" });
  const modelHint = Object.hasOwn(MODEL_HINTS, embeddingType ?? "")
    ? MODEL_HINTS[embeddingType]
    : null;

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
      {modelHint && (
        <Typography
          data-testid="embedding-model-hint"
          fontSize="12px"
          color="text.secondary"
        >
          {modelHint}
        </Typography>
      )}
    </Box>
  );
};

EmbeddingConfigField.propTypes = {
  control: PropTypes.object,
};

export default EmbeddingConfigField;
