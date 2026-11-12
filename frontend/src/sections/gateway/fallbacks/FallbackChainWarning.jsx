/* eslint-disable react/prop-types */
import React from "react";
import { Alert } from "@mui/material";
import { AdminButton as Button } from "../components/GatewayWriteControls";
import { MAX_MODEL_FALLBACKS } from "./fallbackLimits";

export default function FallbackChainWarning({ fallbacks, onTrim }) {
  if (fallbacks.length <= MAX_MODEL_FALLBACKS) return null;
  return (
    <Alert
      severity="warning"
      sx={{ mt: 1 }}
      action={
        <Button color="inherit" size="small" onClick={onTrim}>
          Trim to {MAX_MODEL_FALLBACKS}
        </Button>
      }
    >
      Only the first {MAX_MODEL_FALLBACKS} fallback models are used
    </Alert>
  );
}
