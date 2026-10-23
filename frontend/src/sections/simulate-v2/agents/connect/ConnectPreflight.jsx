import PropTypes from "prop-types";
import { Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const OK = "#16A34A";
const STATE_WORD = { verified: "Verified", missing: "Missing", failed: "Failed" };

/**
 * Preflight on a connect form: the source and each credential, checked
 * before the environment is built. Required when the form builds an
 * environment — Connect and build stays off until it reads "Ready to
 * build" — and optional on a plain connect, which can finish setting up
 * later. A summary line says what was read (files scanned, what was found).
 */
export default function ConnectPreflight({ preflight, required = false, canRun = true, blockedReason = null, summary = null }) {
  const { status, results, ready, run } = preflight;

  if (status !== "done") {
    const running = status === "running";
    return (
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 1.125, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
        {running
          ? <CircularProgress size={14} thickness={5} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          : <Iconify icon="solar:shield-check-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />}
        <Typography sx={{ typography: "s2", flex: 1, minWidth: 0, color: running ? "text.primary" : "text.secondary" }}>
          {running
            ? "Checking the source and credentials…"
            : blockedReason || (required
              ? "Preflight checks the source and credentials before building."
              : "Optional — check the source and credentials now. You can connect without it and finish setting up later.")}
        </Typography>
        <Button
          size="small" variant="outlined" disabled={!canRun || running} onClick={run}
          sx={{ typography: "s3", fontWeight: 700, height: 26, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Run preflight
        </Button>
      </Stack>
    );
  }

  const failed = (results || []).filter((c) => !c.ok).length;
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
        <Iconify
          icon={ready ? "solar:check-circle-bold" : "solar:danger-triangle-bold"} width={16}
          sx={{ color: ready ? OK : "error.main", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            {ready ? "Ready to build" : `Needs attention — ${failed} of ${results.length} check${results.length === 1 ? "" : "s"} failed`}
          </Typography>
          {summary && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{summary}</Typography>}
        </Box>
        <Button
          size="small" onClick={run} startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
          sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", flexShrink: 0 }}
        >
          Re-run
        </Button>
      </Stack>
      {(results || []).map((c, i) => (
        <Stack key={c.id} direction="row" alignItems="flex-start" spacing={1.5} sx={{ px: 1.5, py: 0.875, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{c.label}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", overflowWrap: "anywhere" }}>{c.detail}</Typography>
          </Box>
          <Typography sx={{ typography: "s3", fontWeight: 600, color: c.ok ? OK : "error.main", flexShrink: 0, pt: 0.25 }}>
            {STATE_WORD[c.state] || (c.ok ? "Verified" : "Failed")}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}

ConnectPreflight.propTypes = {
  preflight: PropTypes.shape({ status: PropTypes.string, results: PropTypes.array, ready: PropTypes.bool, run: PropTypes.func }).isRequired,
  required: PropTypes.bool,
  canRun: PropTypes.bool,
  blockedReason: PropTypes.string,
  summary: PropTypes.string,
};
