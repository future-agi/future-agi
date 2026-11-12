import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";

import { SegmentedTabs } from "src/components/tabs/tabs";

import { CopyButton } from "../settings/ui";
import { GATEWAY_MONO } from "../utils/fonts";
import { fillGatewaySnippet } from "./personalGatewaySnippet";

// Code in a few languages, one shown at a time, with a copy button.
export default function QuickstartCode({ snippets, personalKey = "" }) {
  const [active, setActive] = useState(0);
  const template = snippets[active] || snippets[0];
  const current = {
    ...template,
    code: fillGatewaySnippet(template.code, personalKey),
  };
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{ px: 1, py: 0.75, borderBottom: 1, borderColor: "divider" }}
      >
        <SegmentedTabs
          value={active}
          onChange={(_, next) => setActive(next)}
          aria-label="Language"
        >
          {snippets.map((snippet) => (
            <Tab key={snippet.key} label={snippet.label} />
          ))}
        </SegmentedTabs>
        <CopyButton text={current.code} label={`Copy ${current.label}`} />
      </Stack>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 2,
          bgcolor: "background.neutral",
          fontFamily: GATEWAY_MONO,
          typography: "s2",
          overflowX: "auto",
          whiteSpace: "pre",
        }}
      >
        {current.code}
      </Box>
    </Box>
  );
}

QuickstartCode.propTypes = {
  personalKey: PropTypes.string,
  snippets: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      code: PropTypes.string.isRequired,
    }),
  ).isRequired,
};
