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
import SvgColor from "src/components/svg-color";
import { copyText } from "./copyText";
import { GATEWAY_MONO } from "../utils/fonts";

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
            <SvgColor
              src="/assets/icons/ic_copy.svg"
              sx={{ width: 16, height: 16 }}
            />
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
          fontFamily: GATEWAY_MONO,
          fontSize: "0.75rem",
          lineHeight: 1.6,
        }}
      >
        <Box component="code" sx={{ fontFamily: GATEWAY_MONO }}>
          {current?.code}
        </Box>
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
