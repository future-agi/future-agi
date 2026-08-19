/**
 * Loading and provisioning states.
 *
 * Two rules drive these: never show a bare spinner where we know the actual
 * work being done, and never show a blank rectangle where we know the shape of
 * what is arriving. A user waiting on a sandbox VM should be able to read what
 * the platform is doing on their behalf.
 */
import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Skeleton, CircularProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import { pulse, shimmer } from "./primitives";

/* ── staged boot sequence ────────────────────────────────────────────────── */

/**
 * Walks a list of provisioning steps, marking each done in turn. Used whenever
 * we are standing something real up: environment boot, agent handshake, or the
 * pre-flight before a run.
 */
export function BootSequence({ steps, stepMs = 900, onDone, accent = "#7857FC", compact }) {
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    if (current >= steps.length) {
      const t = setTimeout(() => onDone?.(), 320);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setCurrent((c) => c + 1), stepMs);
    return () => clearTimeout(t);
  }, [current, steps.length, stepMs, onDone]);

  return (
    <Stack spacing={compact ? 0.75 : 1.25} sx={{ width: "100%" }}>
      {steps.map((label, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <Stack
            key={label}
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              opacity: done || active ? 1 : 0.38,
              transition: "opacity .3s ease",
            }}
          >
            <Box sx={{ width: 18, height: 18, display: "grid", placeItems: "center", flexShrink: 0 }}>
              {done ? (
                <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: "#16A34A" }} />
              ) : active ? (
                <CircularProgress size={13} thickness={5.5} sx={{ color: accent }} />
              ) : (
                <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "text.disabled" }} />
              )}
            </Box>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: active ? 600 : 400,
                color: done ? "text.secondary" : active ? "text.primary" : "text.disabled",
              }}
            >
              {label}
              {active && <AnimatedEllipsis />}
            </Typography>
          </Stack>
        );
      })}
    </Stack>
  );
}
BootSequence.propTypes = {
  steps: PropTypes.array.isRequired, stepMs: PropTypes.number,
  onDone: PropTypes.func, accent: PropTypes.string, compact: PropTypes.bool,
};

export function AnimatedEllipsis() {
  const [n, setN] = useState(1);
  useEffect(() => {
    const t = setInterval(() => setN((x) => (x % 3) + 1), 420);
    return () => clearInterval(t);
  }, []);
  return <Box component="span" sx={{ color: "text.subtitle" }}>{".".repeat(n)}</Box>;
}

/* ── full-panel provisioning splash ──────────────────────────────────────── */

export function ProvisioningPanel({ title, subtitle, steps, accent = "#7857FC", onDone, icon }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      spacing={3}
      sx={{ py: 8, px: 3, width: "100%" }}
    >
      <Box sx={{ position: "relative", display: "grid", placeItems: "center" }}>
        {[0, 1, 2].map((i) => (
          <Box
            key={i}
            sx={{
              position: "absolute",
              width: 64 + i * 26,
              height: 64 + i * 26,
              borderRadius: "50%",
              border: "1px solid",
              borderColor: (t) => alpha(accent, t.palette.mode === "dark" ? 0.22 : 0.16),
              animation: `${pulse} 2.4s ease-in-out infinite`,
              animationDelay: `${i * 0.35}s`,
            }}
          />
        ))}
        <Box
          sx={{
            width: 64, height: 64, borderRadius: 2, display: "grid", placeItems: "center",
            bgcolor: (t) => alpha(accent, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: accent,
            border: () => `1px solid ${alpha(accent, 0.28)}`,
          }}
        >
          <Iconify icon={icon || "solar:server-square-linear"} width={30} />
        </Box>
      </Box>

      <Box sx={{ textAlign: "center" }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>{title}</Typography>
        {subtitle && (
          <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.5 }}>{subtitle}</Typography>
        )}
      </Box>

      <Box sx={{ width: "100%", maxWidth: 300 }}>
        <BootSequence steps={steps} accent={accent} onDone={onDone} />
      </Box>
    </Stack>
  );
}
ProvisioningPanel.propTypes = {
  title: PropTypes.string, subtitle: PropTypes.string, steps: PropTypes.array,
  accent: PropTypes.string, onDone: PropTypes.func, icon: PropTypes.string,
};

/* ── shaped skeletons ────────────────────────────────────────────────────── */

export function EnvironmentCardSkeleton() {
  return (
    <Box sx={{ p: 2.5, border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper" }}>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 1.5 }}>
        <Skeleton variant="rounded" width={40} height={40} />
        <Box flex={1}>
          <Skeleton variant="text" width="55%" height={20} />
          <Skeleton variant="text" width="80%" height={14} />
        </Box>
      </Stack>
      <Skeleton variant="text" width="100%" height={14} />
      <Skeleton variant="text" width="70%" height={14} />
      <Stack direction="row" spacing={0.75} sx={{ mt: 1.5 }}>
        <Skeleton variant="rounded" width={54} height={20} />
        <Skeleton variant="rounded" width={68} height={20} />
        <Skeleton variant="rounded" width={44} height={20} />
      </Stack>
    </Box>
  );
}

export function RowSkeleton({ rows = 6 }) {
  return (
    <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
      {Array.from({ length: rows }).map((_, i) => (
        <Stack key={i} direction="row" alignItems="center" spacing={2} sx={{ px: 2, py: 1.5 }}>
          <Skeleton variant="circular" width={22} height={22} />
          <Box flex={1}>
            <Skeleton variant="text" width={`${40 + ((i * 13) % 35)}%`} height={16} />
            <Skeleton variant="text" width={`${25 + ((i * 7) % 30)}%`} height={12} />
          </Box>
          <Skeleton variant="rounded" width={48} height={20} />
        </Stack>
      ))}
    </Stack>
  );
}
RowSkeleton.propTypes = { rows: PropTypes.number };

/* ── inline "thinking" bar, for generation flows ─────────────────────────── */

export function ThinkingBar({ label = "Thinking", accent = "#7857FC" }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ py: 1 }}>
      <Box
        sx={{
          width: 90, height: 4, borderRadius: 2, overflow: "hidden",
          background: `linear-gradient(90deg, ${alpha(accent, 0.12)} 0%, ${alpha(accent, 0.55)} 50%, ${alpha(accent, 0.12)} 100%)`,
          backgroundSize: "320px 100%",
          animation: `${shimmer} 1.3s linear infinite`,
        }}
      />
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        {label}
        <AnimatedEllipsis />
      </Typography>
    </Stack>
  );
}
ThinkingBar.propTypes = { label: PropTypes.string, accent: PropTypes.string };
