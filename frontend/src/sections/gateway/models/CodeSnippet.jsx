import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { copyText } from "./copyText";

// A request example in one of several languages, with a copy button.
const CodeSnippet = ({ snippets, ariaLabel = "Code example" }) => {
  const [active, setActive] = useState(snippets[0]?.key);
  const current = snippets.find((s) => s.key === active) || snippets[0];

  const handleCopy = () => {
    copyText(current.code);
  };

  return (
    <Box
      sx={{
        borderRadius: 1,
        border: 1,
        borderColor: "divider",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ px: 1, py: 0.5, borderBottom: 1, borderColor: "divider" }}
      >
        <ToggleButtonGroup
          value={current.key}
          exclusive
          size="small"
          aria-label={ariaLabel}
          onChange={(_, value) => value && setActive(value)}
        >
          {snippets.map((s) => (
            <ToggleButton
              key={s.key}
              value={s.key}
              sx={{
                px: 1.25,
                py: 0.25,
                border: 0,
                textTransform: "none",
                fontSize: "0.75rem",
              }}
            >
              {s.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Tooltip title="Copy" arrow>
          <IconButton size="small" onClick={handleCopy} aria-label="Copy code">
            <Iconify icon="mdi:content-copy" width={16} />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 2,
          bgcolor: "background.neutral",
          overflowX: "auto",
          fontFamily: "monospace",
          fontSize: "0.75rem",
          lineHeight: 1.6,
        }}
      >
        <code>{current.code}</code>
      </Box>
    </Box>
  );
};

CodeSnippet.propTypes = {
  snippets: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      code: PropTypes.string.isRequired,
    }),
  ).isRequired,
  ariaLabel: PropTypes.string,
};

export default CodeSnippet;
