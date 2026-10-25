import React from "react";
import PropTypes from "prop-types";
import { IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { copyText } from "../models/copyText";

/**
 * Copies a request's generation id, the id GET /v1/generation looks up.
 * Renders nothing for a log from before log entry v2, which has none.
 */
export default function GenerationIdCopyButton({ generationId }) {
  if (!generationId) return null;
  return (
    <Tooltip title="Copy generation id" placement="top" arrow>
      <IconButton
        size="small"
        aria-label="Copy generation id"
        onClick={() => copyText(generationId, "Generation id copied")}
      >
        <Iconify icon="mdi:content-copy" width={16} />
      </IconButton>
    </Tooltip>
  );
}

GenerationIdCopyButton.propTypes = { generationId: PropTypes.string };
