import { Box, Link, Typography } from "@mui/material";
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
import { useEmbeddingAccess } from "src/sections/develop-detail/Common/modelAccess";
import { buildEmbeddingTypeOptions } from "./embeddingTypeOptions";
import {
  EMBEDDING_CREDITS_DISCLOSURE,
  EMBEDDING_PAYER_HINT,
  SENTENCE_TRANSFORMERS_UNAVAILABLE,
} from "./retrievalCopy";

// What model serving loads for each Hugging Face-backed type, shown before
// submit (validation.js refuses any other ID with the server's message).
const MODEL_HINTS = {
  huggingface: `Approved models: ${APPROVED_HUGGINGFACE_EMBEDDING_MODELS.join(", ")}`,
  // Model serving has no sentence_transformers model (it answers 404).
  sentence_transformers: SENTENCE_TRANSFORMERS_UNAVAILABLE,
};

const EmbeddingConfigField = ({ control }) => {
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);

  const { data } = useApiKeysStatus({});
  const { data: embeddingAccess } = useEmbeddingAccess();
  const embeddingType = useWatch({ control, name: "embeddingConfig.type" });
  const modelHint = Object.hasOwn(MODEL_HINTS, embeddingType ?? "")
    ? MODEL_HINTS[embeddingType]
    : null;

  // One key (R1-04): a type is enabled by its managed-or-BYOK entitlement;
  // with no verdict from the server the provider-key rule is unchanged.
  const EmbeddingType = useMemo(
    () =>
      buildEmbeddingTypeOptions({
        providerStatus: data,
        access: embeddingAccess,
      }),
    [data, embeddingAccess],
  );
  const isManagedType = Boolean(
    EmbeddingType.find((option) => option.value === embeddingType)?.managed,
  );
  // Why each type the user cannot pick is disabled, shown before it is picked;
  // a reason the model hint already shows is not repeated.
  const typeReasons = EmbeddingType.filter(
    (option) => option.disabled && option.reason && option.reason !== modelHint,
  );

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
      {typeReasons.map((option) => (
        <Typography
          key={option.value}
          data-testid="embedding-type-reason"
          fontSize="12px"
          color="text.secondary"
        >
          {option.reason}
        </Typography>
      ))}
      <Typography data-testid="embedding-managed-disclosure" fontSize="12px" color="text.secondary">
        {EMBEDDING_CREDITS_DISCLOSURE}{" "}
        <Link
          component="button"
          type="button"
          underline="hover"
          onClick={() => setApiConfigurationOpen(true)}
          sx={{ verticalAlign: "baseline" }}
        >
          Configure API keys
        </Link>
      </Typography>
      {isManagedType && (
        <Typography
          data-testid="embedding-payer-hint"
          fontSize="12px"
          color="text.secondary"
        >
          {EMBEDDING_PAYER_HINT}
        </Typography>
      )}
    </Box>
  );
};

EmbeddingConfigField.propTypes = {
  control: PropTypes.object,
};

export default EmbeddingConfigField;
