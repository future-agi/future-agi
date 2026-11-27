import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import LogExcerpt from "../../components/logs/LogExcerpt";
import LogsDrawer from "../../components/logs/LogsDrawer";
import { agentHostOf, checkLogOf, firstErrorOf } from "../../_mock/sandboxLogs";
import { sandboxFailOf } from "./preflight";

const OK = "status.success.fg";
const STATE_WORD = {
  verified: "Verified",
  missing: "Missing",
  failed: "Failed",
};

/**
 * Preflight on a connect form: the source and each credential, checked
 * before the environment is built. Required when the form builds an
 * environment — Connect and build stays off until it reads "Ready to
 * build" — and optional on a plain connect, which can finish setting up
 * later. A summary line says what was read (files scanned, what was found).
 */
export default function ConnectPreflight({
  preflight,
  required = false,
  canRun = true,
  blockedReason = null,
  summary = null,
  logAgent = null,
}) {
  const { status, results, ready, run } = preflight;
  const [logsOpen, setLogsOpen] = useState(false);
  /* A log exists only where our sandbox ran the code. */
  const sandboxed = !!logAgent && agentHostOf(logAgent).kind === "sandbox";
  const failAt = sandboxed ? sandboxFailOf() : null;
  const log = useMemo(
    () =>
      sandboxed && status === "done"
        ? checkLogOf({ agent: logAgent, failAt })
        : null,
    [sandboxed, status, logAgent, failAt],
  );

  if (status !== "done") {
    const running = status === "running";
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 1.5,
          py: 1.125,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {running ? (
          <CircularProgress
            size={14}
            thickness={5}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
        ) : (
          <Iconify
            icon="solar:shield-check-linear"
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            typography: "s2",
            flex: 1,
            minWidth: 0,
            color: running ? "text.primary" : "text.secondary",
          }}
        >
          {running
            ? "Checking the source and credentials…"
            : blockedReason ||
              (required
                ? "Checks the source and credentials."
                : "Optional — check the source and credentials now. You can connect without it and finish setting up later.")}
        </Typography>
        {/* The next step while the build waits for it: the purple button.
            Once it has passed, Connect takes over as the main action. */}
        <Button
          size="small"
          variant={required && !ready ? "contained" : "outlined"}
          color={required && !ready ? "primary" : "inherit"}
          disabled={!canRun || running}
          onClick={run}
          sx={{
            flexShrink: 0,
            ...(!(required && !ready) && {
              color: "text.primary",
              borderColor: "divider",
            }),
          }}
        >
          Run preflight
        </Button>
      </Stack>
    );
  }

  const failed = (results || []).filter((c) => !c.ok).length;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon={
            ready ? "solar:check-circle-bold" : "solar:danger-triangle-bold"
          }
          width={16}
          sx={{ color: ready ? OK : "error.main", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {ready
              ? "Ready to build"
              : `Needs attention — ${failed} of ${results.length} check${results.length === 1 ? "" : "s"} failed`}
          </Typography>
          {summary && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {summary}
            </Typography>
          )}
        </Box>
        {log && (
          <Button
            size="small"
            onClick={() => setLogsOpen(true)}
            sx={{ color: "text.primary", flexShrink: 0 }}
          >
            Logs
          </Button>
        )}
        <Button
          variant="outlined"
          size="small"
          onClick={run}
          startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
          sx={{ flexShrink: 0 }}
        >
          Re-run
        </Button>
      </Stack>
      {(results || []).map((c, i) => (
        <Stack
          key={c.id}
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.875,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {c.label}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                overflowWrap: "anywhere",
              }}
            >
              {c.detail}
            </Typography>
          </Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: c.ok ? OK : "error.main",
              flexShrink: 0,
              pt: 0.25,
            }}
          >
            {STATE_WORD[c.state] || (c.ok ? "Verified" : "Failed")}
          </Typography>
        </Stack>
      ))}
      {log && failAt && (
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <LogExcerpt log={log} onViewAll={() => setLogsOpen(true)} />
        </Box>
      )}
      <LogsDrawer
        open={logsOpen}
        onClose={() => setLogsOpen(false)}
        log={log}
        title={log?.title}
        subtitle="Sandbox · removed after the check"
        focusLineId={failAt ? firstErrorOf(log)?.line?.id : undefined}
      />
    </Box>
  );
}

ConnectPreflight.propTypes = {
  preflight: PropTypes.shape({
    status: PropTypes.string,
    results: PropTypes.array,
    ready: PropTypes.bool,
    run: PropTypes.func,
  }).isRequired,
  required: PropTypes.bool,
  canRun: PropTypes.bool,
  blockedReason: PropTypes.string,
  summary: PropTypes.string,
  /* The code agent being connected: its sandbox log opens from the result. */
  logAgent: PropTypes.object,
};
