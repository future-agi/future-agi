import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Collapse from "@mui/material/Collapse";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import SvgColor from "src/components/svg-color";

import { DetailRow } from "../../components/frame/DetailRows";
import { hasEnforcement } from "../enforcement";
import EnforcementNote from "./EnforcementNote";

// Settings this gateway stores but never applies, shown read-only: the "Not
// enforced on this gateway" chip with its reason, then each stored value as a
// row. It holds no field, switch, picker or option, so nothing here looks
// like it works; the tab that renders it keeps the stored values, so a save
// sends them back unchanged. A secret reads only "Set" or "Not set"
// (storedValues.js secretState). With no id left in enforcement.js (the
// gateway applies them all) the block renders nothing.
//
// The container carries data-read-only: the guard test
// (__tests__/notEnforcedReadOnly.test.jsx) checks that every not-enforced
// note sits in one and that it holds no enabled control.

const DEFAULT_TITLE = "Recorded only";
const DEFAULT_DESCRIPTION =
  "Stored with this configuration and not read by the gateway.";

export default function RecordedOnly({
  ids,
  rows,
  testId,
  title = DEFAULT_TITLE,
  description = DEFAULT_DESCRIPTION,
  emptyText,
  collapsible = false,
  sx,
}) {
  const [open, setOpen] = useState(!collapsible);
  const bodyId = useId();
  if (!ids.some(hasEnforcement)) return null;
  if (!rows.length && !emptyText) return null;

  const body = (
    <Stack spacing={1.5}>
      <EnforcementNote ids={ids} />
      {rows.length ? (
        <Box>
          {rows.map((row) => (
            <DetailRow
              key={row.key || row.label}
              label={row.label}
              mono={row.mono}
            >
              {row.value}
            </DetailRow>
          ))}
        </Box>
      ) : (
        <Typography typography="s2" color="text.secondary">
          {emptyText}
        </Typography>
      )}
    </Stack>
  );

  return (
    <Paper
      variant="outlined"
      component="section"
      aria-label={title}
      data-testid={testId}
      data-read-only=""
      sx={{ p: 2, borderRadius: 1, ...sx }}
    >
      <Stack spacing={1.5}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 1.5,
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              component="h3"
              typography="s1"
              fontWeight="fontWeightMedium"
            >
              {title}
            </Typography>
            {description != null && (
              <Typography typography="s2" color="text.secondary">
                {description}
              </Typography>
            )}
          </Box>
          {collapsible && (
            <IconButton
              size="small"
              aria-label={title}
              aria-expanded={open}
              aria-controls={bodyId}
              onClick={() => setOpen((value) => !value)}
              sx={{ flexShrink: 0 }}
            >
              <SvgColor
                src="/assets/icons/custom/lucide--chevron-down.svg"
                sx={{
                  width: 16,
                  height: 16,
                  color: "text.secondary",
                  transform: open ? "rotate(180deg)" : "none",
                }}
              />
            </IconButton>
          )}
        </Box>
        {collapsible ? (
          <Collapse in={open} id={bodyId}>
            {body}
          </Collapse>
        ) : (
          body
        )}
      </Stack>
    </Paper>
  );
}

RecordedOnly.propTypes = {
  // The enforcement ids (settings/enforcement.js) whose notes the block shows.
  ids: PropTypes.arrayOf(PropTypes.string).isRequired,
  rows: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      label: PropTypes.string.isRequired,
      value: PropTypes.node,
      mono: PropTypes.bool,
    }),
  ).isRequired,
  testId: PropTypes.string,
  title: PropTypes.string,
  description: PropTypes.node,
  // With no rows: shown in their place. Without it, no rows renders nothing.
  emptyText: PropTypes.node,
  // Starts collapsed behind a disclosure button (a long list at the bottom).
  collapsible: PropTypes.bool,
  sx: PropTypes.object,
};
