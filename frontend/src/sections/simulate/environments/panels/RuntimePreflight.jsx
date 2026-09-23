import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { STATUS_META } from "../myEnvironments.constants";

// The real preflight returns these five checks in this order; sort defensively
// so the list reads the same regardless of response ordering.
const CHECK_ORDER = [
  "source",
  "credentials_present",
  "credential_files",
  "credentials_valid",
  "provider_target",
];

const CHECK_GLYPH = {
  passed: "solar:check-circle-bold",
  failed: "solar:close-circle-bold",
  skipped: "solar:minus-circle-linear",
};

const toneFor = (status) => STATUS_META[status]?.color || STATUS_META.not_run.color;

// A compact state pill for the card header — same silhouette as StatusPill
// (dot + label, tinted border/bg) but with the preflight's own copy.
function StatePill({ connected }) {
  const color = connected ? BUILD_TONES.green : BUILD_TONES.red;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{
        px: 0.875,
        py: 0.375,
        borderRadius: 999,
        border: "1px solid",
        borderColor: alpha(color, 0.35),
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.14 : 0.09),
        flexShrink: 0,
      }}
    >
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: color }} />
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color }}>
        {connected ? "Connected" : "Blocked"}
      </Typography>
    </Stack>
  );
}
StatePill.propTypes = { connected: PropTypes.bool };

// "N passed · N failed · N skipped" — the read-audit StatChip strip, inlined.
function CountStrip({ passed, failed, skipped }) {
  const item = (value, label, color) => (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: color || "text.disabled" }} />
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: color || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
    </Stack>
  );
  return (
    <Stack direction="row" flexWrap="wrap" alignItems="center" spacing={2} rowGap={0.5}>
      {item(passed, "passed", passed ? BUILD_TONES.green : null)}
      {item(failed, "failed", failed ? BUILD_TONES.red : null)}
      {item(skipped, "skipped", null)}
    </Stack>
  );
}
CountStrip.propTypes = {
  passed: PropTypes.number,
  failed: PropTypes.number,
  skipped: PropTypes.number,
};

// The red "why this is blocked + how to fix it" block under a failed check —
// the SectionIssue pattern, retinted to red and carrying the missing aliases.
function FailBlock({ missing = [], fix }) {
  if (!missing.length && !fix) return null;
  return (
    <Box
      sx={{
        mt: 1,
        px: 1.5,
        py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.35 : 0.3),
        bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      {missing.length > 0 && (
        <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: fix ? 1 : 0 }}>
          {missing.map((alias) => (
            <Box
              key={alias}
              sx={{
                px: 0.75,
                py: 0.125,
                borderRadius: 0.75,
                border: "1px solid",
                borderColor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.45 : 0.35),
              }}
            >
              <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: BUILD_TONES.red }}>
                {alias}
              </Typography>
            </Box>
          ))}
        </Stack>
      )}
      {fix && (
        <Stack direction="row" alignItems="flex-start" spacing={1}>
          <Iconify icon="solar:key-minimalistic-linear" width={14} sx={{ color: BUILD_TONES.red, flexShrink: 0, mt: "2px" }} />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            <Box component="span" sx={{ fontWeight: "fontWeightBold", color: "text.primary" }}>Fix: </Box>
            {fix}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
FailBlock.propTypes = { missing: PropTypes.arrayOf(PropTypes.string), fix: PropTypes.string };

// One check row: status glyph · label · mono id · detail, and a fail block when
// the check failed. Mirrors the FactSection row (py, faint divider, spacing).
function CheckRow({ check }) {
  const color = toneFor(check.status);
  return (
    <Box sx={{ py: 1 }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
        <Iconify icon={CHECK_GLYPH[check.status] || CHECK_GLYPH.skipped} width={16} sx={{ color, flexShrink: 0, mt: "1px" }} />
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap">
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
              {check.label}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontFamily: "ui-monospace, Menlo, monospace" }}>
              {check.id}
            </Typography>
          </Stack>
          {check.detail && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
              {check.detail}
            </Typography>
          )}
          {check.status === "failed" && (
            <FailBlock missing={check.missing} fix={check.fix} />
          )}
        </Box>
      </Stack>
    </Box>
  );
}
CheckRow.propTypes = { check: PropTypes.object };

/**
 * Inline preflight for a source panel. `status` is the panel's local machine:
 *   idle  → the "Run preflight" trigger + one-line prompt
 *   running → the trigger, disabled, with a spinner
 *   error → the request threw; a retry
 *   done  → the SectionCard-framed checks list (summary + 5 rows)
 * Colours come from STATUS_META / BUILD_TONES; no new hex.
 */
export default function RuntimePreflight({
  status = "idle",
  canRun = false,
  onRun,
  checks,
  state,
  error,
}) {
  // idle / running: the trigger only — no card chrome before there's a result.
  if (status === "idle" || status === "running") {
    const running = status === "running";
    return (
      <Stack spacing={0.75}>
        <Button
          variant="outlined"
          size="small"
          disabled={!canRun || running}
          onClick={onRun}
          startIcon={
            running ? (
              <CircularProgress size={13} thickness={5} color="inherit" />
            ) : (
              <Iconify icon="solar:shield-check-linear" width={15} />
            )
          }
          sx={{ typography: "s2", fontWeight: "fontWeightBold", alignSelf: "flex-start" }}
        >
          {running ? "Checking source and credentials…" : "Run preflight"}
        </Button>
        {!running && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            We check the source and credentials before building.
          </Typography>
        )}
      </Stack>
    );
  }

  if (status === "error") {
    return (
      <Box
        sx={{
          px: 1.5,
          py: 1.25,
          borderRadius: 1,
          border: "1px solid",
          borderColor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.35 : 0.3),
          bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.08 : 0.05),
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5}>
          <Stack direction="row" alignItems="flex-start" spacing={1.25} minWidth={0}>
            <Iconify icon="solar:danger-triangle-linear" width={15} sx={{ color: BUILD_TONES.red, flexShrink: 0, mt: "2px" }} />
            <Box minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>Preflight couldn&apos;t run</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
                {error?.message || "Something went wrong. Try again."}
              </Typography>
            </Box>
          </Stack>
          <Button
            size="small"
            variant="outlined"
            onClick={onRun}
            startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
            sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.primary", borderColor: "divider", flexShrink: 0, "&:hover": { borderColor: "text.primary", bgcolor: "transparent" } }}
          >
            Try again
          </Button>
        </Stack>
      </Box>
    );
  }

  // done — tolerate a missing/empty checks array (backend divergence).
  const rows = Array.isArray(checks) ? checks : [];
  if (!rows.length) {
    return (
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        Preflight returned no checks.
      </Typography>
    );
  }
  const ordered = [...rows].sort(
    (a, b) => CHECK_ORDER.indexOf(a.id) - CHECK_ORDER.indexOf(b.id),
  );
  const connected = state === "connected";
  const passed = rows.filter((c) => c.status === "passed").length;
  const failed = rows.filter((c) => c.status === "failed").length;
  const skipped = rows.filter((c) => c.status === "skipped").length;

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
      {/* header */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1.5}
        sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>Preflight</Typography>
        <Stack direction="row" alignItems="center" spacing={1}>
          <StatePill connected={connected} />
          <Button
            size="small"
            onClick={onRun}
            startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
            sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary", "&:hover": { bgcolor: "transparent", color: "text.primary" } }}
          >
            Re-run
          </Button>
        </Stack>
      </Stack>

      {/* summary */}
      <Box sx={{ px: 1.75, pt: 1.5, pb: 1.25 }}>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", color: connected ? BUILD_TONES.green : "text.primary" }}>
          {connected ? "Ready to build" : `${failed} check${failed === 1 ? "" : "s"} to resolve`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, mb: 1.25 }}>
          {connected
            ? "Everything checks out — nothing is blocking the build."
            : "Fix the flagged items below, then re-run preflight."}
        </Typography>
        <CountStrip passed={passed} failed={failed} skipped={skipped} />
      </Box>

      {/* rows */}
      <Box sx={{ px: 1.75, pb: 0.75 }}>
        <Stack divider={<Divider sx={{ borderColor: (t) => alpha(t.palette.divider, 0.6) }} />}>
          {ordered.map((check) => (
            <CheckRow key={check.id} check={check} />
          ))}
        </Stack>
      </Box>
    </Box>
  );
}
RuntimePreflight.propTypes = {
  status: PropTypes.oneOf(["idle", "running", "done", "error"]),
  canRun: PropTypes.bool,
  onRun: PropTypes.func,
  checks: PropTypes.array,
  state: PropTypes.oneOf(["connected", "failed"]),
  error: PropTypes.shape({ message: PropTypes.string }),
};
