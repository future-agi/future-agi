/* eslint-disable react/prop-types */
import React from "react";
import { Stack, Card, CardContent, MenuItem, Typography } from "@mui/material";
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
}) => {
  const [requestRemoval, confirmation] = useConfirmRemoval();
  const dynamicModels = useAvailableModels();
  const models = modelsProp || dynamicModels;
  const handlePrimaryChange = (e) => {
    onChange(e.target.value, fallbacks);
  };

  const handleFallbackChange = (index, value) => {
    const updated = [...fallbacks];
    updated[index] = value;
    onChange(primaryModel, updated);
  };

  const atLimit = fallbacks.length >= MAX_MODEL_FALLBACKS;
  const handleAddFallback = () => {
    if (atLimit) return;
    onChange(primaryModel, [...fallbacks, ""]);
  };

  const handleRemoveFallback = (index) => {
    const updated = fallbacks.filter((_, i) => i !== index);
    onChange(primaryModel, updated);
  };

  return (
    <Card variant="outlined" sx={{ mb: 1 }}>
      {confirmation}
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
          <TextField
            select
            size="small"
            value={primaryModel}
            onChange={handlePrimaryChange}
            sx={{ minWidth: 160 }}
            label="Primary"
          >
            {models.map((m) => (
              <MenuItem key={m} value={m}>
                {m}
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
                {models.map((m) => (
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

          <Button
            size="small"
            onClick={handleAddFallback}
            disabled={atLimit}
            startIcon={<Glyph name="add" />}
          >
            Add
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
      </CardContent>
    </Card>
  );
};

export default FallbackChainEditor;
