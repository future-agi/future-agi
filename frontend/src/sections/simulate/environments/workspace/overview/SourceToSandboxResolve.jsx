import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, Radio } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { MAP_COPY } from "./sourceToSandbox.constants";

const MONO = "ui-monospace, Menlo, monospace";

// The "still open" marker that stands in for a sandbox target the reader could
// not derive on its own.
export function NeedsAnswerLabel() {
  return (
    <StatusBadge status="needs_input" label={MAP_COPY.needsAnswer} variant="dot" />
  );
}

// The badge a row carries once the reader has confirmed its effect.
export function ConfirmedChip() {
  return <StatusBadge status="confirmed" role="success" label={MAP_COPY.confirmed} size="sm" dot={false} />;
}

// Flat inline resolver anchored to the row it belongs to: the question, why the
// reader couldn't answer it, two option pills, then confirm + a secondary.
export function InlineResolve({ toolName, onPick }) {
  const [pick, setPick] = useState("reads");
  const { options } = MAP_COPY.resolve;
  const picked = options.find((o) => o.id === pick);

  return (
    <Box sx={{ maxWidth: 560 }}>
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Iconify icon="solar:question-circle-linear" width={14} sx={{ color: "text.subtitle", position: "relative", top: 2 }} />
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>
          Does <Box component="span" sx={{ fontFamily: MONO }}>{toolName}</Box> change data?
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, ml: 3 }}>
        {MAP_COPY.resolve.why}
      </Typography>

      <Stack direction="row" spacing={1} sx={{ mt: 1.5, ml: 3 }}>
        {options.map((o) => {
          const selected = pick === o.id;
          return (
            <Box
              key={o.id}
              role="button"
              tabIndex={0}
              onClick={() => setPick(o.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setPick(o.id); }}
              sx={{
                flex: 1, borderRadius: 1, px: 1.5, py: 1,
                border: "1px solid",
                borderColor: selected ? "text.secondary" : "divider",
                bgcolor: selected ? "action.selected" : "transparent",
                cursor: "pointer",
                display: "flex", alignItems: "center", gap: 1,
                transition: "border-color 120ms, background-color 120ms",
                "&:hover": { borderColor: selected ? "text.secondary" : "text.disabled" },
              }}
            >
              <Radio size="small" checked={selected} tabIndex={-1} readOnly sx={{ p: 0 }} />
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="baseline" spacing={0.5}>
                  <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>{o.label}</Typography>
                  {o.suggested && (
                    <Typography sx={{ typography: "s3", color: "text.subtitle", fontStyle: "italic" }}>
                      suggested
                    </Typography>
                  )}
                </Stack>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                  {o.detail}
                </Typography>
              </Box>
            </Box>
          );
        })}
      </Stack>

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5, ml: 3 }}>
        <Button
          size="small"
          variant="contained"
          onClick={() => onPick?.(pick)}
        >
          {MAP_COPY.resolve.confirm(picked.label)}
        </Button>
        <Button
          size="small"
          variant="text"
          color="inherit"
        >
          {MAP_COPY.resolve.secondary}
        </Button>
      </Stack>
    </Box>
  );
}
InlineResolve.propTypes = {
  toolName: PropTypes.string,
  onPick: PropTypes.func,
};
