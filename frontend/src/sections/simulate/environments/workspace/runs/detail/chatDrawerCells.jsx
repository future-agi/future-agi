import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

// The three small primitives the ported chat call drawer paints with: a meta
// chip (right-pane header), an analytics grid cell and an attribute row. Kept
// together so the drawer frame and the transcript pane share one vocabulary.

export function Meta({ label, value }) {
  return (
    <Stack
      direction="row"
      spacing={0.5}
      sx={{ px: 1, py: 0.5, borderRadius: 0.75, border: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label} :</Typography>
      <Typography sx={{ typography: "s3", color: "text.primary", fontWeight: 500 }}>
        {value}
      </Typography>
    </Stack>
  );
}
Meta.propTypes = { label: PropTypes.string, value: PropTypes.node };

export function Cell({ label, value }) {
  return (
    <Box sx={{ p: 1.75, borderBottom: "1px solid", borderRight: "1px solid", borderColor: "divider" }}>
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontWeight: 700,
          color: "text.subtitle",
          letterSpacing: 0.3,
          textTransform: "uppercase",
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{ typography: "m2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", lineHeight: 1.3 }}
      >
        {value}
      </Typography>
    </Box>
  );
}
Cell.propTypes = { label: PropTypes.string, value: PropTypes.node };

export function Attr({ label, value }) {
  return (
    <Stack direction="row" spacing={2} sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
      <Typography
        sx={{
          width: 96,
          flexShrink: 0,
          typography: "s3",
          color: "text.subtitle",
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      >
        {label}
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{value || "—"}</Typography>
    </Stack>
  );
}
Attr.propTypes = { label: PropTypes.string, value: PropTypes.node };
