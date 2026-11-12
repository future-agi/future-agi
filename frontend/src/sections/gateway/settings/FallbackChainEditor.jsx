/* eslint-disable react/prop-types */
import Section from "src/components/section";
import React, { useState } from "react";
import { Stack, MenuItem, Typography } from "@mui/material";
import {
  AdminButton as Button,
  AdminIconButton as IconButton,
  AdminTextField as TextField,
} from "../components/GatewayWriteControls";
import { useConfirmRemoval } from "../hooks/useConfirmRemoval";
import { useAvailableModels } from "../hooks/useAvailableModels";
import { Glyph } from "./ui";
import FallbackChainWarning from "../fallbacks/FallbackChainWarning";
import {
  MAX_MODEL_FALLBACKS,
  FALLBACK_LIMIT_MESSAGE,
} from "../fallbacks/fallbackLimits";

const FallbackChainEditor = ({
  primaryModel,
  fallbacks,
  onChange,
  onRemoveChain = undefined,
  models: modelsProp = undefined,
  usedPrimaries = [],
}) => {
  const [requestRemoval, confirmation] = useConfirmRemoval();
  const [newFallback, setNewFallback] = useState("");
  const dynamicModels = useAvailableModels();
  const models = modelsProp || dynamicModels;
  const primaryChoices = Array.from(new Set([primaryModel, ...models]));
  const handlePrimaryChange = (e) => {
    const next = e.target.value;
    if (!next.trim() || (next !== primaryModel && usedPrimaries.includes(next)))
      return;
    onChange(next, fallbacks);
  };

  const handleFallbackChange = (index, value) => {
    if (!value.trim()) return;
    const updated = [...fallbacks];
    updated[index] = value;
    onChange(primaryModel, updated);
  };

  const atLimit = fallbacks.length >= MAX_MODEL_FALLBACKS;
  const handleAddFallback = () => {
    if (atLimit || !newFallback.trim() || !models.includes(newFallback)) return;
    onChange(primaryModel, [...fallbacks, newFallback]);
    setNewFallback("");
  };

  const handleRemoveFallback = (index) => {
    const updated = fallbacks.filter((_, i) => i !== index);
    onChange(primaryModel, updated);
  };

  return (
    <Section variant="panel" sx={{ mb: 1 }}>
      {confirmation}

      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <TextField
          select
          size="small"
          value={primaryModel}
          onChange={handlePrimaryChange}
          sx={{ minWidth: 160 }}
          label="Primary"
        >
          {primaryChoices.map((m) => (
            <MenuItem
              key={m}
              value={m}
              disabled={m !== primaryModel && usedPrimaries.includes(m)}
            >
              {m}
              {m !== primaryModel && usedPrimaries.includes(m)
                ? " (already has a chain)"
                : ""}
            </MenuItem>
          ))}
        </TextField>

        {fallbacks.map((fb, i) => (
          <React.Fragment key={i}>
            <Glyph name="arrow" sx={{ color: "text.secondary" }} />
            <TextField
              select
              size="small"
              value={fb}
              onChange={(e) => handleFallbackChange(i, e.target.value)}
              sx={{ minWidth: 160 }}
              label={`Fallback ${i + 1}`}
            >
              {Array.from(new Set([fb, ...models])).map((m) => (
                <MenuItem key={m} value={m}>
                  {m}
                </MenuItem>
              ))}
            </TextField>
            <IconButton
              size="small"
              aria-label={`Remove fallback ${i + 1}`}
              onClick={() =>
                requestRemoval(`Fallback ${fb} from ${primaryModel}`, () =>
                  handleRemoveFallback(i),
                )
              }
            >
              <Glyph name="close" />
            </IconButton>
          </React.Fragment>
        ))}

        <TextField
          select
          size="small"
          label="New fallback"
          inputProps={{ "aria-label": `New fallback for ${primaryModel}` }}
          value={models.includes(newFallback) ? newFallback : ""}
          onChange={(e) => setNewFallback(e.target.value)}
          disabled={atLimit || models.length === 0}
          sx={{ minWidth: 160 }}
        >
          <MenuItem value="" disabled>
            Choose a model
          </MenuItem>
          {models.map((model) => (
            <MenuItem key={model} value={model}>
              {model}
            </MenuItem>
          ))}
        </TextField>
        <Button
          size="small"
          onClick={handleAddFallback}
          disabled={
            atLimit || !newFallback.trim() || !models.includes(newFallback)
          }
          startIcon={<Glyph name="add" />}
        >
          Add fallback
        </Button>

        {onRemoveChain && (
          <IconButton
            size="small"
            aria-label={`Remove fallback chain ${primaryModel}`}
            onClick={() =>
              requestRemoval(`Fallback chain ${primaryModel}`, onRemoveChain)
            }
            sx={{ ml: "auto" }}
          >
            <Glyph name="delete" sx={{ color: "error.main" }} />
          </IconButton>
        )}
      </Stack>
      {!atLimit && (
        <Typography variant="caption" color="text.secondary">
          Choose a model, then select Add fallback to append it. Only added
          models are saved.
        </Typography>
      )}
      <FallbackChainWarning
        fallbacks={fallbacks}
        onTrim={() =>
          onChange(primaryModel, fallbacks.slice(0, MAX_MODEL_FALLBACKS))
        }
      />
      {atLimit && (
        <Typography variant="caption" color="text.secondary" role="status">
          {FALLBACK_LIMIT_MESSAGE}
        </Typography>
      )}
    </Section>
  );
};

export default FallbackChainEditor;
