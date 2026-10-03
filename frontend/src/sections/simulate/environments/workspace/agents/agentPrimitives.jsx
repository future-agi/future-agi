import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, IconButton, Tooltip, Chip } from "@mui/material";

import Iconify from "src/components/iconify";

import { AGENT_ROW_SHAPE } from "./agents.shapes";
import { SOURCE_ACCENT, AGENT_CARD_COPY } from "./agentCards.constants";

// Small outlined button used in card headers and version blocks. Style is
// consistent across all row actions so the group reads as a coherent bar. When
// `tint` is passed, the icon + border take that colour; otherwise neutral
// (divider border, text-secondary content). Hover deepens the tint background.
export function ChipButton({ icon, label, tint, onClick }) {
  return (
    <Button
      size="small" variant="outlined" onClick={onClick}
      startIcon={<Iconify icon={icon} width={14} />}
      sx={{
        height: 26, minHeight: 26, minWidth: 0, px: 1, borderRadius: 1,
        typography: "s3", fontWeight: "fontWeightBold",
        color: tint || "text.secondary",
        borderColor: (t) => (tint ? alpha(tint, 0.35) : t.palette.divider),
        bgcolor: (t) => (tint ? alpha(tint, t.palette.mode === "dark" ? 0.06 : 0.03) : "transparent"),
        "& .MuiButton-startIcon": { mr: 0.5, ml: 0 },
        "&:hover": {
          borderColor: (t) => (tint ? alpha(tint, 0.6) : t.palette.text.subtitle),
          bgcolor: (t) => (tint ? alpha(tint, t.palette.mode === "dark" ? 0.14 : 0.08) : t.palette.action.hover),
        },
      }}
    >
      {label}
    </Button>
  );
}
ChipButton.propTypes = {
  icon: PropTypes.string, label: PropTypes.string,
  tint: PropTypes.string, onClick: PropTypes.func,
};

// Icon-only variant of ChipButton, for destructive / secondary actions where
// the icon alone is unambiguous (e.g. remove). Same shell as ChipButton so the
// row's action group stays visually consistent.
export function ChipIconButton({ icon, tooltip, onClick }) {
  return (
    <Tooltip arrow title={tooltip || ""}>
      <IconButton
        size="small" onClick={onClick} aria-label={tooltip}
        sx={{
          width: 26, height: 26, borderRadius: 1,
          border: "1px solid", borderColor: "divider",
          color: "text.subtitle",
          "&:hover": {
            borderColor: "text.subtitle",
            bgcolor: "action.hover",
            color: "text.primary",
          },
        }}
      >
        <Iconify icon={icon} width={14} />
      </IconButton>
    </Tooltip>
  );
}
ChipIconButton.propTypes = {
  icon: PropTypes.string, tooltip: PropTypes.string, onClick: PropTypes.func,
};

// A titled block of key/value rows. Used for the connection / source / issued
// credentials sections; the same shape everywhere so those read uniformly.
export function DetailBlock({ title, icon, rows, subtitle }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
        <Iconify icon={icon} width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", letterSpacing: 0.5, fontWeight: "fontWeightBold" }}>
          {title.toUpperCase()}
        </Typography>
      </Stack>
      {subtitle && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.75 }}>
          {subtitle}
        </Typography>
      )}
      <Box sx={{
        borderRadius: 1.25,
        border: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral",
        p: 1.25,
      }}>
        <Stack spacing={0.75}>
          {rows.map((r, i) => <PropRow key={r.label + i} {...r} />)}
        </Stack>
      </Box>
    </Box>
  );
}
DetailBlock.propTypes = {
  title: PropTypes.string, icon: PropTypes.string,
  rows: PropTypes.arrayOf(AGENT_ROW_SHAPE), subtitle: PropTypes.string,
};

// One label/value row inside a DetailBlock, with an optional copy action.
export function PropRow({ label, value, mono, copy }) {
  return (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 0.25, sm: 1.5 }} alignItems={{ sm: "center" }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", width: { sm: 130 }, flexShrink: 0 }}>
        {label}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={0.75} flex={1} minWidth={0}>
        <Typography sx={{
          typography: "s3", color: "text.primary",
          flex: 1, minWidth: 0, wordBreak: "break-word",
          fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
        }}>
          {value}
        </Typography>
        {copy && (
          <Tooltip arrow title={AGENT_CARD_COPY.copy}>
            <IconButton
              size="small" aria-label={AGENT_CARD_COPY.copy}
              onClick={() => navigator.clipboard?.writeText(String(value))}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="solar:copy-linear" width={13} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}
PropRow.propTypes = {
  label: PropTypes.string, value: PropTypes.node,
  mono: PropTypes.bool, copy: PropTypes.bool,
};

// Neutral-toned version chip between the agent name and the role pill. Tinted
// stronger when the agent carries more than one version so the stack is visible.
export function VersionChip({ label, multi }) {
  return (
    <Box sx={{
      display: "inline-flex", alignItems: "center",
      height: 18, borderRadius: 0.75, px: 0.75,
      typography: "s3", fontWeight: "fontWeightBold", letterSpacing: 0.3,
      fontFamily: "ui-monospace, Menlo, monospace",
      color: multi ? SOURCE_ACCENT : "text.subtitle",
      border: "1px solid",
      borderColor: (t) => (multi ? alpha(SOURCE_ACCENT, 0.35) : t.palette.divider),
      bgcolor: (t) => (multi
        ? alpha(SOURCE_ACCENT, t.palette.mode === "dark" ? 0.14 : 0.08)
        : "transparent"),
    }}>
      {label}
    </Box>
  );
}
VersionChip.propTypes = { label: PropTypes.string, multi: PropTypes.bool };

// A small filled pill marking a card's role (env source / active for runs).
export function RolePill({ label, tint }) {
  return (
    <Chip
      size="small" label={label}
      sx={{
        height: 18, borderRadius: 0.75,
        bgcolor: (t) => alpha(tint, t.palette.mode === "dark" ? 0.18 : 0.1),
        color: tint,
        border: "1px solid", borderColor: alpha(tint, 0.35),
        "& .MuiChip-label": {
          px: 0.75, typography: "s3",
          fontWeight: "fontWeightBold", letterSpacing: 0.4,
        },
      }}
    />
  );
}
RolePill.propTypes = { label: PropTypes.string, tint: PropTypes.string };

// A numeric stat with an optional trailing label.
export function StatPill({ value, label }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.375}>
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {label && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {label}
        </Typography>
      )}
    </Stack>
  );
}
StatPill.propTypes = { value: PropTypes.node, label: PropTypes.string };

// One impact line in the promote dialog: icon, title, explanatory body.
export function ImpactRow({ icon, title, body }) {
  return (
    <Stack direction="row" alignItems="flex-start" spacing={1.25}>
      <Iconify icon={icon} width={15} sx={{ color: "text.subtitle", mt: "2px", flexShrink: 0 }} />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{body}</Typography>
      </Box>
    </Stack>
  );
}
ImpactRow.propTypes = { icon: PropTypes.string, title: PropTypes.string, body: PropTypes.string };
