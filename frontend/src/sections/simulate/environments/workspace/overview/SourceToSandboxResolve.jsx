import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import { MAP_COPY } from "./sourceToSandbox.constants";

const MONO = "ui-monospace, Menlo, monospace";

// The "still open" marker that stands in for a sandbox target the reader could
// not derive on its own.
export function NeedsAnswerLabel() {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main" }} />
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "primary.main" }}>
        {MAP_COPY.needsAnswer}
      </Typography>
    </Stack>
  );
}

// The badge a row carries once the reader has confirmed its effect.
export function ConfirmedChip() {
  return (
    <Chip
      size="small"
      label={MAP_COPY.confirmed}
      sx={{
        height: 18, borderRadius: 0.75,
        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: "fontWeightBold", letterSpacing: 0.2 },
        color: "primary.main",
        bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.18 : 0.1),
      }}
    />
  );
}

// Flat inline resolver anchored to the row it belongs to: the question, why the
// reader couldn't answer it, two option pills, then confirm + a secondary.
export function InlineResolve({ toolName, onPick }) {
  const [pick, setPick] = useState("reads");
  const { options } = MAP_COPY.resolve;
  const picked = options.find((o) => o.id === pick);

  return (
    <Box sx={{ maxWidth: 560 }}>
      <Stack direction="row" alignItems="baseline" spacing={0.75}>
        <Iconify icon="solar:question-circle-linear" width={14} sx={{ color: "primary.main", position: "relative", top: 2 }} />
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
          Does <Box component="span" sx={{ fontFamily: MONO }}>{toolName}</Box> change data?
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, ml: 2.5 }}>
        {MAP_COPY.resolve.why}
      </Typography>

      <Stack direction="row" spacing={1} sx={{ mt: 1.25, ml: 2.5 }}>
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
                flex: 1, borderRadius: 1, px: 1.25, py: 0.875,
                border: "1px solid",
                borderColor: selected ? "primary.main" : "divider",
                bgcolor: (t) => (selected
                  ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.14 : 0.06)
                  : "background.paper"),
                cursor: "pointer",
                display: "flex", alignItems: "center", gap: 1,
                transition: "border-color 120ms, background-color 120ms",
                "&:hover": { borderColor: selected ? "primary.main" : "text.disabled" },
              }}
            >
              <Box sx={{
                width: 14, height: 14, borderRadius: "50%", flexShrink: 0,
                border: "1.5px solid",
                borderColor: selected ? "primary.main" : "text.disabled",
                display: "grid", placeItems: "center",
              }}>
                {selected && <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main" }} />}
              </Box>
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="baseline" spacing={0.5}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{o.label}</Typography>
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

      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 1.25, ml: 2.5 }}>
        <Button
          size="small"
          variant="contained"
          onClick={() => onPick?.(pick)}
          sx={{ typography: "s2", fontWeight: "fontWeightBold", px: 1.5, py: 0.5, minHeight: 0 }}
        >
          {MAP_COPY.resolve.confirm(picked.label)}
        </Button>
        <Button
          size="small"
          variant="text"
          sx={{
            typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.subtitle",
            px: 1, py: 0.5, minHeight: 0,
            "&:hover": { bgcolor: "transparent", color: "text.primary" },
          }}
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
