import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import Term from "src/sections/common/ux/Term";
import CopyButton from "src/sections/common/ux/CopyButton";
import CodeBlock from "src/sections/common/Values/CodeBlock";
import { HoverCard } from "src/sections/common/ux/oneview";
import CheckChips from "./CheckChips";

/**
 * `◎ Expected` in the top bar (ONE-VIEW-SPEC A4-12): a hover card with
 * what success looks like (the task's goal as a sentence), each check as a
 * chip with its description on hover, a `Raw JSON` toggle and `Copy`.
 * Clicking pins it. No strip under the top bar (saves 32 px); the checks
 * are also the `Checks` Inspect tab.
 */
export default function ExpectedCard({ row }) {
  const [raw, setRaw] = useState(false);
  if (!row) return null;
  const checks = row.checks ?? [];
  const json = () =>
    JSON.stringify({ expected: row.expected_summary, checks }, null, 2);
  return (
    <HoverCard
      title="Expected"
      maxWidth={420}
      trigger={
        <Box
          component="span"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            typography: "s2",
            color: "text.secondary",
            whiteSpace: "nowrap",
          }}
        >
          <Iconify icon="mdi:target" width={14} />
          Expected
        </Box>
      }
      triggerProps={{ "aria-label": "Expected: what should happen" }}
      triggerSx={{ flexShrink: 0 }}
    >
      <Stack spacing={1} sx={{ minWidth: 300 }}>
        <Typography typography="s2">
          {row.expected_summary ?? "No expected outcome on this task."}
        </Typography>
        {row.input_image?.url && Array.isArray(row.expected_box) ? (
          // IT-R5 G4 S1: an image-box task shows its labelled box (dashed)
          // on the input photo.
          <Box
            data-testid="expected-box"
            sx={{
              position: "relative",
              width: 240,
              aspectRatio: `${row.input_image.width || 640} / ${row.input_image.height || 480}`,
              borderRadius: 0.5,
              overflow: "hidden",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <img
              src={row.input_image.url}
              alt={row.input_image.alt ?? "Input photo"}
              style={{ width: "100%", height: "100%", display: "block" }}
            />
            {(() => {
              const [x, y, w, h] = row.expected_box;
              const W = row.input_image.width || 640;
              const H = row.input_image.height || 480;
              return (
                <Box
                  sx={{
                    position: "absolute",
                    left: `${(x / W) * 100}%`,
                    top: `${(y / H) * 100}%`,
                    width: `${(w / W) * 100}%`,
                    height: `${(h / H) * 100}%`,
                    border: "2px dashed #16a34a",
                  }}
                />
              );
            })()}
          </Box>
        ) : null}
        {checks.length ? (
          <Box>
            <Typography typography="s3" color="text.secondary" sx={{ mb: 0.5 }}>
              <Term k="check">Checks</Term>
            </Typography>
            <CheckChips checks={checks} wrap />
          </Box>
        ) : null}
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Button
            size="small"
            color="inherit"
            onClick={() => setRaw((v) => !v)}
            startIcon={<Iconify icon="mdi:code-json" width={14} />}
            sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
          >
            {raw ? "Hide raw JSON" : "Raw JSON"}
          </Button>
          <Box sx={{ flex: 1 }} />
          <CopyButton
            value={json}
            tooltip="Copy the expected outcome as JSON"
          />
        </Stack>
        {raw ? (
          <CodeBlock text={json()} language="json" maxHeight={220} />
        ) : null}
      </Stack>
    </HoverCard>
  );
}

ExpectedCard.propTypes = { row: PropTypes.object };
