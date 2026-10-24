import PropTypes from "prop-types";
import { Box, Chip, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useDemoMode } from "./demoMode";

/*
  Small pieces the agent page's newer cards share — the same card, row and
  tag the Connections and Constraints tabs already draw by hand.
*/

/**
 * A bordered card with a one-line header: title, an optional note or action.
 * `bare` drops the header when the section around it already names it.
 */
export function PanelCard({ title, note, action, children, sx, bare }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
        ...sx,
      }}
    >
      {!bare && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
          >
            {title}
          </Typography>
          {note && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
            >
              {note}
            </Typography>
          )}
          {action}
        </Stack>
      )}
      {children}
    </Box>
  );
}
PanelCard.propTypes = {
  title: PropTypes.node,
  note: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
  bare: PropTypes.bool,
};

/** The full sentence behind a quiet ⓘ — so a row keeps to one line. */
export function InfoTip({ text }) {
  if (!text) return null;
  return (
    <Tooltip arrow title={text}>
      <Box
        component="span"
        sx={{
          display: "inline-flex",
          verticalAlign: "middle",
          ml: 0.5,
          color: "text.disabled",
          cursor: "help",
        }}
      >
        <Iconify icon="solar:info-circle-linear" width={14} />
      </Box>
    </Tooltip>
  );
}
InfoTip.propTypes = { text: PropTypes.node };

/** Label on the left, value on the right — any longer note behind an ⓘ. */
export function KvRow({ label, children, hint, first, align = "flex-start" }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      alignItems={align}
      sx={{
        px: 2,
        py: 1,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 150,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box flex={1} minWidth={0}>
        {typeof children === "string" ? (
          <Typography sx={{ typography: "s2" }}>
            {children}
            <InfoTip text={hint} />
          </Typography>
        ) : !hint ? (
          children
        ) : (
          <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>
            <Box sx={{ minWidth: 0, flex: "0 1 auto" }}>{children}</Box>
            <InfoTip text={hint} />
          </Stack>
        )}
      </Box>
    </Stack>
  );
}
KvRow.propTypes = {
  label: PropTypes.node,
  children: PropTypes.node,
  hint: PropTypes.node,
  first: PropTypes.bool,
  align: PropTypes.string,
};

/**
 * A quiet inline tag — "Phase 2", "Coming soon", "Main", "Outside change".
 * Neutral by default; `tone="error"` is kept for blocking states.
 */
export function QuietTag({ children, tone, icon, title }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      color={tone === "error" ? "error" : "default"}
      title={title}
      label={children}
      icon={icon ? <Iconify icon={icon} width={12} /> : undefined}
      sx={{ flexShrink: 0 }}
    />
  );
}
QuietTag.propTypes = {
  children: PropTypes.node,
  tone: PropTypes.oneOf(["error"]),
  icon: PropTypes.string,
  title: PropTypes.string,
};

/** Marks a staging-only control ("Simulate a push") — shown only with ?demo=1. */
export function DemoLabel() {
  const demo = useDemoMode();
  if (!demo) return null;
  return (
    <Typography
      component="span"
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.disabled",
        mr: 0.75,
      }}
    >
      Demo
    </Typography>
  );
}

/** The primary-coloured inline action the agent page uses ("Sync now", "Edit"). */
export function ActionText({ onClick, children, disabled }) {
  return (
    <Typography
      component="span"
      role="button"
      tabIndex={disabled ? -1 : 0}
      onClick={disabled ? undefined : onClick}
      onKeyDown={(e) => {
        if (!disabled && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onClick?.();
        }
      }}
      sx={{
        typography: "s3",
        fontWeight: 600,
        flexShrink: 0,
        color: disabled ? "text.disabled" : "primary.main",
        cursor: disabled ? "default" : "pointer",
        "&:hover": disabled ? {} : { textDecoration: "underline" },
      }}
    >
      {children}
    </Typography>
  );
}
ActionText.propTypes = {
  onClick: PropTypes.func,
  children: PropTypes.node,
  disabled: PropTypes.bool,
};
