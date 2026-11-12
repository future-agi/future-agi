import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";

// A probe the deployment declined to send (Future AGI Cloud never contacts a
// customer's LiveKit URL) neither passed nor failed, and does not block the run.
const probeVerdict = (probe) => {
  if (probe.skipped)
    return { text: "not verified on hosted", color: "text.secondary" };
  return probe.ok
    ? { text: "verified", color: "status.success.fg" }
    : { text: "failed", color: "status.danger.fg" };
};

const CREDENTIAL_CHECKS = new Set([
  "credentials_present",
  "credential_files",
  "credentials_valid",
  "provider_target",
]);

// Head a result that is not ready by what blocked it: an unreadable or
// unbuildable source is not a credential problem.
const notReadyHeading = (failedIds) => {
  if (failedIds.has("source")) return "Source can't be read";
  if (failedIds.has("packaging")) return "Source isn't buildable";
  if ([...failedIds].some((id) => CREDENTIAL_CHECKS.has(id)))
    return "Credentials need attention";
  if (failedIds.has("platform_dialer")) return "Outbound dialer isn't set up";
  if (failedIds.size) return "Preflight checks failed";
  return "Credentials need attention";
};

// The server's check details start lowercase; each one is shown as a sentence.
const asSentence = (text) => {
  const value = String(text || "").trim();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
};

// The sandbox egress guard refuses private and local hosts with an operator's
// sentence: "private/reserved host 'localhost' is not allowed in egress". That
// host can come from what was entered or from this server's own settings (its
// public address, the model gateway), so the panel says what happened in plain
// words, gives both ways forward, and keeps the raw text behind Details.
const EGRESS_REFUSED = "egress_domain_private";

const egressRefusal = (error, message) => {
  const code = error?.error ?? error?.response?.data?.error;
  if (code !== EGRESS_REFUSED && !/is not allowed in egress/.test(message))
    return null;
  // The server quotes the host the way Python's repr does: '...' or "...".
  const host = /host\s+['"]([^'"]+)['"]/.exec(message)?.[1] || "";
  return { host };
};

const EGRESS_NEXT_STEP =
  "If you entered a private or local address, use a public repository or host URL, or switch to Code upload. If everything you entered is public, this server's own settings point at a private address, so ask whoever runs it to change them.";

// Any other failure keeps the server's text and gains a way forward.
const PREFLIGHT_ERROR_NEXT_STEP =
  "Check what you entered above, then try again.";

export default function RuntimePreflight({
  status = "idle",
  canRun = false,
  blockedReason = null,
  onRun,
  result,
  error,
}) {
  const [showDetails, setShowDetails] = useState(false);

  if (status === "idle" || status === "running") {
    const running = status === "running";
    return (
      <Stack spacing={0.75}>
        <Button
          variant="contained"
          color="primary"
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
          sx={{ alignSelf: "flex-start" }}
        >
          {running ? "Checking source and credentials…" : "Run preflight"}
        </Button>
        {!running && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {blockedReason ||
              "We check the source and credentials before building."}
          </Typography>
        )}
      </Stack>
    );
  }

  if (status === "error") {
    const message = errorMessage(error);
    const egress = egressRefusal(error, message);
    return (
      <Box
        sx={{
          px: 1.5,
          py: 1.25,
          borderRadius: "8px",
          bgcolor: "status.danger.bg",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={1.5}
        >
          <Box minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 500 }}>
              Preflight couldn&apos;t run
            </Typography>
            {egress ? (
              <>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {egress.host
                    ? `This server blocks private and local addresses, so it can't reach ${egress.host}.`
                    : "This server blocks private and local addresses."}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {EGRESS_NEXT_STEP}
                </Typography>
                <Button
                  size="small"
                  aria-expanded={showDetails}
                  onClick={() => setShowDetails((open) => !open)}
                  sx={{ px: 0, minWidth: 0, typography: "s3" }}
                >
                  Details
                </Button>
                {showDetails && (
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {message}
                  </Typography>
                )}
              </>
            ) : (
              <>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {message}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {PREFLIGHT_ERROR_NEXT_STEP}
                </Typography>
              </>
            )}
          </Box>
          {/* The text beside it wraps; the button keeps its whole label. */}
          <Button
            size="small"
            variant="outlined"
            onClick={onRun}
            startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
            sx={{ flexShrink: 0 }}
          >
            Try again
          </Button>
        </Stack>
      </Box>
    );
  }

  const credentials = result?.credentials;
  const ready = result?.ready_to_submit === true && !!credentials;
  const requirements = (credentials?.requirements || []).filter(
    (item) => item.required || item.status === "configured",
  );
  const probes = credentials?.probe || [];
  const choices = (credentials?.credential_choices || []).filter(
    (choice) => !choice.satisfied,
  );
  const failedChecks = (
    Array.isArray(result?.checks) ? result.checks : []
  ).filter((check) => check?.status === "failed");
  const failedIds = new Set(failedChecks.map((check) => check.id));
  const sourceUnread = failedIds.has("source");

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1.5}
        sx={{
          px: 1.5,
          minHeight: 36,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s1_2", fontWeight: 500 }}>
          Preflight
        </Typography>
        <Button
          size="small"
          onClick={onRun}
          startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
        >
          Re-run
        </Button>
      </Stack>
      <Stack spacing={1.25} sx={{ p: 1.5 }} divider={<Divider />}>
        <Box>
          <Typography
            sx={{
              typography: "s1_2",
              fontWeight: 500,
              color: ready ? "status.success.fg" : "status.danger.fg",
            }}
          >
            {ready ? "Ready to build" : notReadyHeading(failedIds)}
          </Typography>
          {credentials && !sourceUnread && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {credentials.scanned_files ?? 0} source files scanned
              {credentials.detected_connectors?.length
                ? ` · Detected: ${credentials.detected_connectors.join(", ")}`
                : ""}
            </Typography>
          )}
        </Box>
        {!ready &&
          failedChecks.map((check) => {
            const detail = asSentence(check.detail);
            const fix =
              check.fix && check.fix !== check.detail ? check.fix : "";
            return (
              <Stack
                key={check.id || check.label}
                direction="row"
                justifyContent="space-between"
                spacing={1}
              >
                <Box minWidth={0}>
                  <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                    {check.label}
                  </Typography>
                  {detail && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.subtitle",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {detail}
                    </Typography>
                  )}
                  {fix && (
                    <Typography
                      sx={{ typography: "s3", color: "status.danger.fg" }}
                    >
                      {fix}
                    </Typography>
                  )}
                </Box>
                <Typography
                  sx={{ typography: "s3", color: "status.danger.fg" }}
                >
                  failed
                </Typography>
              </Stack>
            );
          })}
        {requirements.map((item) => (
          <Stack
            key={`${item.environment_name}:${item.purpose}`}
            direction="row"
            justifyContent="space-between"
            spacing={1}
          >
            <Box minWidth={0}>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: 500,
                  fontFamily: "ui-monospace, Menlo, monospace",
                  overflowWrap: "anywhere",
                }}
              >
                {item.environment_name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {item.purpose}
                {item.kind === "file" ? " · Credential file" : ""}
              </Typography>
            </Box>
            <Typography
              sx={{
                typography: "s3",
                color:
                  item.status === "missing"
                    ? "status.danger.fg"
                    : "status.success.fg",
              }}
            >
              {item.status}
            </Typography>
          </Stack>
        ))}
        {choices.map((choice) => (
          <Typography
            key={choice.id}
            sx={{ typography: "s3", color: "status.danger.fg" }}
          >
            Provide one credential set:{" "}
            {choice.options.map((option) => option.join(" + ")).join(" or ")}
          </Typography>
        ))}
        {probes.map((probe) => {
          const verdict = probeVerdict(probe);
          return (
            <Box key={`${probe.provider}:${probe.aliases.join(",")}`}>
              <Typography sx={{ typography: "s1", color: verdict.color }}>
                {probe.label}: {verdict.text}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {probe.message}
              </Typography>
            </Box>
          );
        })}
        {!credentials && (
          <Typography sx={{ typography: "s3", color: "status.danger.fg" }}>
            Preflight did not return a credential report. Re-run before
            building.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

RuntimePreflight.propTypes = {
  status: PropTypes.oneOf(["idle", "running", "done", "error"]),
  canRun: PropTypes.bool,
  blockedReason: PropTypes.string,
  onRun: PropTypes.func,
  result: PropTypes.shape({
    ready_to_submit: PropTypes.bool,
    checks: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        label: PropTypes.string,
        status: PropTypes.string,
        detail: PropTypes.string,
        fix: PropTypes.string,
      }),
    ),
    credentials: PropTypes.object,
  }),
  error: PropTypes.object,
};
