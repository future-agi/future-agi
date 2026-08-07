import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Collapse, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const cardSx = {
  border: "1px solid",
  borderColor: "orange.o20",
  borderRadius: 0.5,
  overflow: "hidden",
  mt: 0.5,
};

const headerSx = {
  display: "flex",
  alignItems: "center",
  gap: 0.5,
  px: 1,
  py: 0.5,
  bgcolor: "orange.o10",
  cursor: "pointer",
};

const bodySx = {
  p: 1,
  bgcolor: "orange.o5",
  maxHeight: 200,
  overflow: "auto",
};

const preSx = {
  typography: "s3",
  fontFamily: "monospace",
  m: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

// Shared collapsible card used for both a tool call (name + arguments) and a
// tool result. Kept generic so the trace view and the simulation transcript
// render tool activity identically.
const CollapsibleToolCard = ({ icon, label, body }) => {
  const [open, setOpen] = useState(false);

  return (
    <Box sx={cardSx}>
      <Box data-search-skip="true" onClick={() => setOpen(!open)} sx={headerSx}>
        <Iconify icon={icon} width={13} sx={{ color: "orange.500" }} />
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          sx={{ color: "orange.500", flex: 1 }}
        >
          {label}
        </Typography>
        <Iconify
          icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={14}
          sx={{ color: "orange.500" }}
        />
      </Box>
      <Collapse in={open}>
        <Box sx={bodySx}>
          <Box component="pre" sx={preSx}>
            {body}
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
};

CollapsibleToolCard.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  body: PropTypes.string,
};

const asText = (value) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);

export const ToolCallCard = ({ toolCall }) => (
  <CollapsibleToolCard
    icon="mdi:wrench-outline"
    label={toolCall.name}
    body={asText(toolCall.arguments)}
  />
);

ToolCallCard.propTypes = {
  toolCall: PropTypes.shape({
    name: PropTypes.string.isRequired,
    arguments: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  }).isRequired,
};

export const ToolResultCard = ({ content }) => (
  <CollapsibleToolCard
    icon="mdi:arrow-left-bottom"
    label="Tool result"
    body={asText(content)}
  />
);

ToolResultCard.propTypes = {
  content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
};

export default ToolCallCard;
