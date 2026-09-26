import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, CircularProgress, Divider, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { BUILD_TONES } from "../buildEnvironment/buildTones";

export default function RuntimePreflight({ status = "idle", canRun = false, onRun, result, error }) {
  if (status === "idle" || status === "running") {
    const running = status === "running";
    return (
      <Stack spacing={0.75}>
        <Button
          variant="outlined"
          size="small"
          disabled={!canRun || running}
          onClick={onRun}
          startIcon={running ? <CircularProgress size={13} thickness={5} color="inherit" /> : <Iconify icon="solar:shield-check-linear" width={15} />}
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
      <Box sx={{ px: 1.5, py: 1.25, borderRadius: 1, border: "1px solid", borderColor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.35 : 0.3) }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5}>
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>Preflight couldn&apos;t run</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{errorMessage(error)}</Typography>
          </Box>
          <Button size="small" variant="outlined" onClick={onRun} startIcon={<Iconify icon="solar:refresh-linear" width={13} />}>
            Try again
          </Button>
        </Stack>
      </Box>
    );
  }

  const credentials = result?.credentials;
  const ready = result?.ready_to_submit === true && !!credentials;
  const requirements = (credentials?.requirements || []).filter((item) => item.required || item.status === "configured");
  const probes = credentials?.probe || [];
  const choices = (credentials?.credential_choices || []).filter((choice) => !choice.satisfied);

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5} sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>Preflight</Typography>
        <Button size="small" onClick={onRun} startIcon={<Iconify icon="solar:refresh-linear" width={13} />}>
          Re-run
        </Button>
      </Stack>
      <Stack spacing={1.25} sx={{ px: 1.75, py: 1.5 }} divider={<Divider />}>
        <Box>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", color: ready ? BUILD_TONES.green : BUILD_TONES.red }}>
            {ready ? "Ready to build" : "Credentials need attention"}
          </Typography>
          {credentials && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {credentials.scanned_files ?? 0} source files scanned
              {credentials.detected_connectors?.length ? ` · Detected: ${credentials.detected_connectors.join(", ")}` : ""}
            </Typography>
          )}
        </Box>
        {requirements.map((item) => (
          <Stack key={`${item.environment_name}:${item.purpose}`} direction="row" justifyContent="space-between" spacing={1}>
            <Box minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", overflowWrap: "anywhere" }}>
                {item.environment_name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {item.purpose}{item.kind === "file" ? " · Credential file" : ""}
              </Typography>
            </Box>
            <Typography sx={{ typography: "s3", color: item.status === "missing" ? BUILD_TONES.red : BUILD_TONES.green }}>
              {item.status}
            </Typography>
          </Stack>
        ))}
        {choices.map((choice) => (
          <Typography key={choice.id} sx={{ typography: "s3", color: BUILD_TONES.red }}>
            Provide one credential set: {choice.options.map((option) => option.join(" + ")).join(" or ")}
          </Typography>
        ))}
        {probes.map((probe) => (
          <Box key={`${probe.provider}:${probe.aliases.join(",")}`}>
            <Typography sx={{ typography: "s2", color: probe.ok ? BUILD_TONES.green : BUILD_TONES.red }}>
              {probe.label}: {probe.ok ? "verified" : "failed"}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{probe.message}</Typography>
          </Box>
        ))}
        {!credentials && (
          <Typography sx={{ typography: "s3", color: BUILD_TONES.red }}>
            Preflight did not return a credential report. Re-run before building.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

RuntimePreflight.propTypes = {
  status: PropTypes.oneOf(["idle", "running", "done", "error"]),
  canRun: PropTypes.bool,
  onRun: PropTypes.func,
  result: PropTypes.shape({ ready_to_submit: PropTypes.bool, credentials: PropTypes.object }),
  error: PropTypes.object,
};
