/**
 * Shared visual primitives for the simulation section.
 *
 * These exist so every screen in the flow reads as one product rather than a
 * pile of MUI defaults — surface identity is carried by colour + icon, and
 * status is carried by a single dot vocabulary used from the gallery all the
 * way down to an individual trace step.
 */
import PropTypes from "prop-types";
import { useState } from "react";
import { ORIGIN_KINDS } from "../_mock/provenance";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Chip,
  Stack,
  Typography,
  Tooltip,
  IconButton,
  LinearProgress,
  keyframes,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";

/* ── animations ──────────────────────────────────────────────────────────── */

export const pulse = keyframes`
  0%   { opacity: 1;   transform: scale(1); }
  50%  { opacity: 0.45; transform: scale(0.82); }
  100% { opacity: 1;   transform: scale(1); }
`;

export const ripple = keyframes`
  0%   { transform: scale(0.7); opacity: 0.55; }
  100% { transform: scale(2.6); opacity: 0; }
`;

export const shimmer = keyframes`
  0%   { background-position: -320px 0; }
  100% { background-position: 320px 0; }
`;

export const dash = keyframes`
  to { stroke-dashoffset: -24; }
`;

/* ── layout ──────────────────────────────────────────────────────────────── */

/**
 * Fluid card grid.
 *
 * Fixed column counts (MUI's `lg={4}`) size cards as a fraction of the
 * container, so a wide screen gives three over-wide cards and a filtered view
 * gives one lonely narrow one. Auto-fill with a minimum instead: the column
 * count follows the available width, and a card never drops below a width its
 * content actually fits in.
 */
export const cardGrid = (min = 360) => ({
  display: "grid",
  gap: 2,
  gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))`,
});

/* ── surface identity ────────────────────────────────────────────────────── */

export function SurfaceIcon({ surface, size = 40, radius = 1.25 }) {
  const s = getSurface(surface);
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: radius,
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        // Neutral by design: the icon says which channel, the colour said it
        // again and made every list look like a paint chart.
        color: "text.secondary",
        bgcolor: "background.neutral",
      }}
    >
      <Iconify icon={s.icon} width={size * 0.52} />
    </Box>
  );
}
SurfaceIcon.propTypes = { surface: PropTypes.string, size: PropTypes.number, radius: PropTypes.number };

export function SurfaceChip({ surface, size = "small" }) {
  const s = getSurface(surface);
  return (
    <Chip
      size={size}
      label={s.label}
      icon={<Iconify icon={s.icon} width={14} />}
      sx={{
        height: 22,
        borderRadius: 0.75,
        color: s.color,
        bgcolor: (t) => alpha(s.color, t.palette.mode === "dark" ? 0.16 : 0.1),
        border: () => `1px solid ${alpha(s.color, 0.24)}`,
        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
        "& .MuiChip-icon": { ml: 0.75, mr: -0.25, color: "inherit" },
      }}
    />
  );
}
SurfaceChip.propTypes = { surface: PropTypes.string, size: PropTypes.string };

/* ── status vocabulary ───────────────────────────────────────────────────── */

export const STATUS_META = {
  queued:    { color: "#71717A", label: "Queued" },
  booting:   { color: "#CA8A04", label: "Provisioning" },
  running:   { color: "#2563EB", label: "Running" },
  grading:   { color: "#7857FC", label: "Grading" },
  passed:    { color: "#16A34A", label: "Passed" },
  /* Samples that disagreed with each other. Neither a pass nor a failure —
     it is the scenario, not the agent, that has been found out. */
  flaky:     { color: "#D97706", label: "Flaky" },
  /* Nothing upstream of the agent worked, so there is no verdict to report.
     Deliberately not red: this is our failure, not the agent's. */
  unmeasured: { color: "#9AA0A6", label: "Not measured" },
  completed: { color: "#71717A", label: "Completed" },
  failed:    { color: "#DC2626", label: "Failed" },
  error:     { color: "#EA580C", label: "Error" },
  cancelled: { color: "#71717A", label: "Cancelled" },
};

/**
 * Verdicts on the comparison screens.
 *
 * Deliberately below the saturation of STATUS_META: there, colour reports an
 * outcome; here, colour is reserved for movement, and a wall of saturated
 * pass/fail blocks drowns the sentence beside them that says what happened.
 *
 * `passes / repeats` is part of the verdict, not a footnote. A scenario that
 * passed two of three samples is not the same finding as one that passed three
 * of three, and a screen that renders both as "Passed" is hiding the only
 * evidence anyone has about how noisy the measurement is.
 */
export const VERDICT_TONE = {
  passed:  { color: "#5AA47B", label: "Passed" },
  failed:  { color: "#C2603F", label: "Failed" },
  flaky:   { color: "#B98A3C", label: "Flaky" },
  unmeasured: { color: "#9AA0A6", label: "Not measured" },
  missing: { color: "#9AA0A6", label: "Not run" },
};

export function Verdict({ status, passes, repeats, showSamples = true }) {
  const tone = VERDICT_TONE[status] || VERDICT_TONE.missing;
  const samples = showSamples && repeats > 1 && passes != null;
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: tone.color, flexShrink: 0 }} />
      <Typography sx={{ typography: "s3", fontWeight: 700, color: tone.color }}>
        {tone.label}
      </Typography>
      {samples && (
        <Typography sx={{ typography: "s3", color: "text.disabled" }}>
          {passes}/{repeats}
        </Typography>
      )}
    </Stack>
  );
}

Verdict.propTypes = {
  status: PropTypes.string,
  passes: PropTypes.number,
  repeats: PropTypes.number,
  showSamples: PropTypes.bool,
};

/**
 * Whose failure it was, said out loud.
 *
 * The whole point of attributing a failure is that somebody reads the
 * attribution — a chip on the row beats a tooltip nobody opens, because the
 * difference between "the agent got this wrong" and "we never measured it" is
 * the difference between changing a prompt and re-running.
 */
export function DomainChip({ domain, dense }) {
  if (!domain) return null;
  return (
    <Tooltip arrow title={`${domain.blurb} ${domain.next}`}>
      <Stack
        direction="row" alignItems="center" spacing={0.5}
        sx={{
          px: dense ? 0.5 : 0.75, py: 0.125, borderRadius: 0.5, flexShrink: 0,
          bgcolor: (t) => alpha(domain.color, t.palette.mode === "dark" ? 0.18 : 0.1),
          cursor: "default",
        }}
      >
        <Box sx={{ width: 5, height: 5, borderRadius: "50%", bgcolor: domain.color, flexShrink: 0 }} />
        <Typography noWrap sx={{ typography: "s3", fontWeight: 700, color: domain.color }}>
          {domain.short}
        </Typography>
      </Stack>
    </Tooltip>
  );
}

DomainChip.propTypes = { domain: PropTypes.object, dense: PropTypes.bool };

export function StatusDot({ status, size = 8, live }) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  const animate = live ?? ["running", "booting", "grading"].includes(status);
  return (
    <Box sx={{ position: "relative", display: "grid", placeItems: "center", width: size * 2, height: size * 2, flexShrink: 0 }}>
      {animate && (
        <Box
          sx={{
            position: "absolute",
            width: size,
            height: size,
            borderRadius: "50%",
            bgcolor: meta.color,
            animation: `${ripple} 1.6s ease-out infinite`,
          }}
        />
      )}
      <Box
        sx={{
          width: size,
          height: size,
          borderRadius: "50%",
          bgcolor: meta.color,
          animation: animate ? `${pulse} 1.6s ease-in-out infinite` : "none",
        }}
      />
    </Box>
  );
}
StatusDot.propTypes = { status: PropTypes.string, size: PropTypes.number, live: PropTypes.bool };

export function StatusChip({ status }) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.25}
      sx={{
        pl: 0.25, pr: 1, height: 22, borderRadius: 0.75,
        color: meta.color,
        bgcolor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.16 : 0.1),
        border: () => `1px solid ${alpha(meta.color, 0.24)}`,
      }}
    >
      <StatusDot status={status} size={6} />
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>{meta.label}</Typography>
    </Stack>
  );
}
StatusChip.propTypes = { status: PropTypes.string };

/* ── score pill ──────────────────────────────────────────────────────────── */

export function ScorePill({ score, passed, label }) {
  const color = passed ? "#16A34A" : "#DC2626";
  return (
    <Tooltip title={label || ""} arrow>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75, height: 20, borderRadius: 0.5,
          color,
          bgcolor: "transparent",
          border: () => `1px solid ${alpha(color, 0.35)}`,
        }}
      >
        <Iconify icon={passed ? "solar:check-circle-bold" : "solar:close-circle-bold"} width={12} />
        <Typography sx={{ typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
          {(score * 100).toFixed(0)}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
ScorePill.propTypes = { score: PropTypes.number, passed: PropTypes.bool, label: PropTypes.string };

/* ── section shell ───────────────────────────────────────────────────────── */

export function SectionCard({ title, subtitle, action, children, sx, dense }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        overflow: "hidden",
        ...sx,
      }}
    >
      {(title || action) && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
          sx={{
            px: dense ? 1.5 : 2.5,
            py: dense ? 1 : 1.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>{title}</Typography>
            {subtitle && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{subtitle}</Typography>
            )}
          </Box>
          {action}
        </Stack>
      )}
      {children}
    </Box>
  );
}
SectionCard.propTypes = {
  title: PropTypes.node, subtitle: PropTypes.node, action: PropTypes.node,
  children: PropTypes.node, sx: PropTypes.object, dense: PropTypes.bool,
};

/* ── copy-to-clipboard field ─────────────────────────────────────────────── */

export function CopyField({ label, value, mono = true, wrap = false }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Box>
      {label && (
        <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 0.5 }}>{label}</Typography>
      )}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5, py: 1, borderRadius: 1,
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Typography
          noWrap={!wrap}
          sx={{
            flex: 1, minWidth: 0,
            typography: "s2",
            ...(wrap && { wordBreak: "break-all" }),
            fontFamily: mono ? "ui-monospace, SFMono-Regular, Menlo, monospace" : undefined,
            color: "text.primary",
          }}
        >
          {value}
        </Typography>
        <Tooltip title={copied ? "Copied" : "Copy"} arrow>
          <IconButton size="small" onClick={copy} sx={{ p: 0.5 }}>
            <Iconify
              icon={copied ? "solar:check-circle-bold" : "solar:copy-linear"}
              width={15}
              sx={{ color: copied ? "#16A34A" : "text.subtitle" }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
    </Box>
  );
}
CopyField.propTypes = {
  label: PropTypes.string, value: PropTypes.string,
  mono: PropTypes.bool, wrap: PropTypes.bool,
};

/* ── metric tile ─────────────────────────────────────────────────────────── */

export function MetricTile({ label, value, sub, color, icon, progress }) {
  return (
    <Box
      sx={{
        p: 2, flex: 1, minWidth: 0,
        border: "1px solid", borderColor: "divider",
        borderRadius: 1.5, bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
        {icon && <Iconify icon={icon} width={14} sx={{ color: color || "text.subtitle" }} />}
        <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>{label}</Typography>
      </Stack>
      <Typography sx={{ typography: "m1", fontWeight: 700, lineHeight: 1.1, color: color || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {sub && <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{sub}</Typography>}
      {progress != null && (
        <LinearProgress
          variant="determinate"
          value={progress}
          sx={{
            mt: 1.25, height: 4, borderRadius: 2,
            bgcolor: (t) => alpha(color || t.palette.text.disabled, 0.16),
            "& .MuiLinearProgress-bar": { bgcolor: color, borderRadius: 2 },
          }}
        />
      )}
    </Box>
  );
}
MetricTile.propTypes = {
  label: PropTypes.string, value: PropTypes.node, sub: PropTypes.node,
  color: PropTypes.string, icon: PropTypes.string, progress: PropTypes.number,
};

/* ── persona chip row ────────────────────────────────────────────────────── */

export function PersonaBadge({ persona, compact }) {
  if (!persona) return null;
  const initials = persona.name.split(" ").map((w) => w[0]).slice(0, 2).join("");
  return (
    <Stack direction="row" alignItems="center" spacing={1} minWidth={0}>
      <Box
        sx={{
          width: compact ? 22 : 28, height: compact ? 22 : 28, borderRadius: "50%",
          display: "grid", placeItems: "center", flexShrink: 0,
          bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
          color: "primary.main",
          typography: "s3", fontWeight: 700,
        }}
      >
        {initials}
      </Box>
      <Box minWidth={0}>
        {/* Customers carry an age and a voice; requesters carry a job title. */}
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
          {persona.name}
          <Box component="span" sx={{ color: "text.subtitle", fontWeight: 400 }}>
            {persona.age ? ` · ${persona.age}` : persona.role ? ` · ${persona.role}` : ""}
          </Box>
        </Typography>
        {!compact && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {[...persona.traits, persona.voice].filter(Boolean).join(" · ")}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
PersonaBadge.propTypes = { persona: PropTypes.object, compact: PropTypes.bool };

/* ── empty state ─────────────────────────────────────────────────────────── */

export function EmptyState({ icon, title, body, action }) {
  return (
    <Stack alignItems="center" justifyContent="center" spacing={1.5} sx={{ py: 8, px: 3, textAlign: "center" }}>
      <Box
        sx={{
          width: 52, height: 52, borderRadius: 1.5, display: "grid", placeItems: "center",
          bgcolor: "background.neutral", color: "text.subtitle",
        }}
      >
        <Iconify icon={icon || "solar:box-minimalistic-linear"} width={26} />
      </Box>
      <Box>
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>{title}</Typography>
        {body && (
          <Typography sx={{ typography: "s2", color: "text.subtitle", maxWidth: 420, mt: 0.5 }}>
            {body}
          </Typography>
        )}
      </Box>
      {action}
    </Stack>
  );
}
EmptyState.propTypes = { icon: PropTypes.string, title: PropTypes.node, body: PropTypes.node, action: PropTypes.node };

/**
 * Where a derived fact was found.
 *
 * Small, because it sits at the end of every tool and rule row — but it is the
 * difference between a rule the code enforces and a sentence somebody left in
 * a README, and those should never look alike. The file and line are the point:
 * a claim you can go and check is a different kind of claim.
 */
export function OriginChip({ origin, file, line, showPath = true }) {
  const meta = ORIGIN_KINDS[origin];
  if (!meta) return null;

  return (
    <Tooltip
      arrow
      placement="top"
      title={
        <Box sx={{ maxWidth: 300, py: 0.5 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, mb: 0.25 }}>{meta.label}</Typography>
          <Typography sx={{ typography: "s3" }}>{meta.note}</Typography>
          {file && (
            <Typography sx={{ typography: "s3", mt: 0.75, fontFamily: "ui-monospace, Menlo, monospace", opacity: 0.75 }}>
              {file}:{line}
            </Typography>
          )}
        </Box>
      }
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexShrink: 0, cursor: "default" }}>
        {showPath && file && (
          <Typography
            noWrap
            sx={{
              typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace",
              display: { xs: "none", lg: "block" }, maxWidth: 300,
            }}
          >
            {file}:{line}
          </Typography>
        )}
        <Typography
          sx={{
            px: 0.75, py: 0.25, borderRadius: 0.5,
            typography: "s3", fontWeight: 700, color: meta.color,
            bgcolor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.16 : 0.1),
          }}
        >
          {meta.short}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
OriginChip.propTypes = {
  origin: PropTypes.string,
  file: PropTypes.string,
  line: PropTypes.number,
  showPath: PropTypes.bool,
};
