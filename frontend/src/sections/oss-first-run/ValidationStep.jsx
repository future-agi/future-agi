import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Typography,
  Link,
  Collapse,
  IconButton,
  Tooltip,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import LoadingButton from "@mui/lab/LoadingButton";
import Iconify from "src/components/iconify";
import { useSetupChecks } from "src/api/ossSetup/oss-setup";
import {
  CHECK_STATUS,
  CHECK_REVEAL_STAGGER_MS,
  CONNECTION_STATE,
} from "./constants";

const { PENDING, PASSED, WARNING, FAILED, SKIPPED } = CHECK_STATUS;

// Fast while the stack boots, then back off so a long outage doesn't hammer it.
const FAST_POLL_MS = 2000;
const SLOW_POLL_MS = 10000;
const FAST_POLL_WINDOW_MS = 30000;

// The server caches a snapshot for 3s, so anything faster is wasted.
const UNSETTLED_POLL_MS = 5000;

// How long a snapshot may go unchanged before polling gives up and leaves it
// to the re-run controls.
const STALL_TIMEOUT_MS = 60000;

const PANEL_MAX_WIDTH = 460;

// `warning.main` disappears on a light surface.
const WARNING_COLOR = "amber.600";

const STATUS_META = {
  [PASSED]: {
    icon: "solar:check-circle-bold",
    color: "success.main",
    label: "Validated",
  },
  [WARNING]: {
    icon: "solar:danger-triangle-bold",
    color: WARNING_COLOR,
    label: "Warning",
  },
  [FAILED]: {
    icon: "solar:close-circle-bold",
    color: "error.main",
    label: "Failed",
  },
  [SKIPPED]: {
    icon: "solar:minus-circle-linear",
    color: "text.disabled",
    label: "Optional",
  },
  [PENDING]: {
    icon: "svg-spinners:90-ring",
    color: "primary.main",
    labelColor: "text.primary",
    label: "Checking…",
  },
};

export default function ValidationStep({
  mode,
  onBack,
  onContinue,
  onProgress,
}) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(true);
  const [revealCount, setRevealCount] = useState(0);
  const [pollInterval, setPollInterval] = useState(FAST_POLL_MS);
  const unreachableSince = useRef(null);
  const timers = useRef([]);
  const hasStaggered = useRef(false);
  const lastSignature = useRef(null);
  const stalledSince = useRef(null);

  const { data, isError, isFetching, refetch, errorUpdatedAt } = useSetupChecks(
    mode,
    { refetchInterval: pollInterval },
  );

  const checks = useMemo(() => data?.checks ?? [], [data]);

  let connectionState = CONNECTION_STATE.CONNECTING;
  if (isError) connectionState = CONNECTION_STATE.UNREACHABLE;
  else if (data) connectionState = CONNECTION_STATE.REACHABLE;
  const reachable = connectionState === CONNECTION_STATE.REACHABLE;

  // Warnings are down services too, just ones this mode tolerates.
  const settled = useMemo(
    () =>
      checks.length > 0 &&
      checks.every((c) => c.status !== FAILED && c.status !== WARNING),
    [checks],
  );

  const signature = useMemo(
    () => checks.map((c) => `${c.id}:${c.status}`).join("|"),
    [checks],
  );

  // Keyed on `errorUpdatedAt`, not `isError`: consecutive failures leave
  // isError identical and the backoff would never engage.
  useEffect(() => {
    if (reachable) {
      unreachableSince.current = null;
      if (settled) {
        setPollInterval(false);
        return;
      }
      // A service that stays down is not necessarily still starting: on a box
      // that is simply missing one, polling would never end. Keep going only
      // while the snapshot is still changing, and give up once it stops.
      if (signature !== lastSignature.current) {
        lastSignature.current = signature;
        stalledSince.current = performance.now();
      }
      const stalledFor = performance.now() - (stalledSince.current ?? 0);
      setPollInterval(
        stalledFor > STALL_TIMEOUT_MS ? false : UNSETTLED_POLL_MS,
      );
      return;
    }
    if (unreachableSince.current === null) {
      unreachableSince.current = performance.now();
    }
    const elapsed = performance.now() - unreachableSince.current;
    setPollInterval(
      elapsed > FAST_POLL_WINDOW_MS ? SLOW_POLL_MS : FAST_POLL_MS,
    );
  }, [reachable, settled, signature, errorUpdatedAt]);

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  // First response only — re-animating every poll would restart the rows under
  // the reader.
  useEffect(() => {
    if (!checks.length) return undefined;
    if (hasStaggered.current) {
      setRevealCount(checks.length);
      return undefined;
    }
    hasStaggered.current = true;
    clearTimers();
    checks.forEach((_, i) => {
      timers.current.push(
        setTimeout(() => setRevealCount(i + 1), i * CHECK_REVEAL_STAGGER_MS),
      );
    });
    return clearTimers;
  }, [checks, clearTimers]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  useEffect(() => {
    onProgress?.(checks.length ? revealCount / checks.length : 0);
  }, [revealCount, checks.length, onProgress]);

  const resolved = useMemo(
    () => checks.slice(0, revealCount),
    [checks, revealCount],
  );
  const stillRevealing = revealCount < checks.length;

  // The stagger flips each row's status rather than adding rows, so the card
  // never grows under the reader's cursor.
  const displayChecks = useMemo(
    () =>
      checks.map((check, i) =>
        i < revealCount ? check : { ...check, status: PENDING, detail: "" },
      ),
    [checks, revealCount],
  );

  const counts = useMemo(() => {
    const c = { passed: 0, warning: 0, failed: 0, optional: 0 };
    resolved.forEach((check) => {
      if (check.status === PASSED) c.passed += 1;
      else if (check.status === WARNING) c.warning += 1;
      else if (check.status === FAILED) c.failed += 1;
      else if (check.status === SKIPPED) c.optional += 1;
    });
    return c;
  }, [resolved]);

  const summary = useMemo(() => {
    if (!reachable) {
      return "Waiting for the server to come up. This is normal on a first run.";
    }
    const parts = [];
    if (counts.passed) parts.push(`${counts.passed} successful`);
    if (counts.warning) parts.push(`${counts.warning} warning`);
    if (counts.failed) parts.push(`${counts.failed} failed`);
    if (counts.optional) parts.push(`${counts.optional} optional`);
    return parts.join(", ") || "Running checks…";
  }, [counts, reachable]);

  const amberMain = theme.palette.amber[600];
  const tint = (key, opacity = 0.16) => alpha(theme.palette[key].main, opacity);

  // An unreachable server shows a spinner, never a wall of failed checks.
  let summaryIcon = {
    icon: "svg-spinners:90-ring-with-bg",
    color: "text.secondary",
    bg: alpha(theme.palette.text.primary, 0.08),
  };
  if (reachable && counts.failed) {
    summaryIcon = {
      icon: "solar:close-circle-bold",
      color: "error.main",
      bg: tint("error"),
    };
  } else if (reachable && counts.warning) {
    summaryIcon = {
      icon: "solar:danger-triangle-bold",
      color: WARNING_COLOR,
      bg: alpha(amberMain, 0.16),
    };
  } else if (reachable) {
    summaryIcon = {
      icon: "solar:check-circle-bold",
      color: "success.main",
      bg: tint("success"),
    };
  }

  // No check result blocks; the only bar is having a snapshot at all.
  const blocked = !reachable || !checks.length;

  const rerunDisabled = isFetching || stillRevealing;

  const renderHead = (
    <Stack sx={{ mb: 2.5 }}>
      <Typography
        variant="l2"
        component="h1"
        fontWeight="fontWeightSemiBold"
        sx={{ color: "text.primary" }}
      >
        Validate your setup
      </Typography>
      <Typography
        variant="s1_2"
        sx={{ color: "text.secondary", maxWidth: PANEL_MAX_WIDTH, mt: 1 }}
      >
        Validation runs immediately. You can re-run any check that fails. If you
        get stuck, see the{" "}
        <Link
          href="https://docs.futureagi.com"
          target="_blank"
          rel="noopener"
          underline="always"
        >
          self-host guide
        </Link>
        .
      </Typography>
    </Stack>
  );

  const renderRow = (check) => {
    const meta = STATUS_META[check.status] || STATUS_META[PENDING];
    const failed = check.status === FAILED;
    const canRerun = check.status === WARNING || failed;
    return (
      <Stack
        key={check.id}
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: failed ? tint("error", 0.28) : "divider",
          bgcolor: failed ? tint("error", 0.1) : "transparent",
        }}
      >
        <Box
          sx={{
            width: 22,
            display: "flex",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Iconify icon={meta.icon} width={22} sx={{ color: meta.color }} />
        </Box>

        <Stack sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="s1"
            fontWeight="fontWeightMedium"
            sx={{ color: "text.primary" }}
          >
            {check.label}
          </Typography>
          {check.detail && (
            <Typography variant="s2" sx={{ color: "text.secondary" }}>
              {check.detail}
            </Typography>
          )}
        </Stack>

        {canRerun && (
          <Tooltip title="Re-run checks">
            <span>
              <IconButton
                size="small"
                disabled={rerunDisabled}
                onClick={() => refetch()}
                sx={{ color: "text.primary", flexShrink: 0 }}
              >
                <Iconify icon="solar:refresh-linear" width={16} />
              </IconButton>
            </span>
          </Tooltip>
        )}

        <Typography
          variant="s2_1"
          fontWeight="fontWeightSemiBold"
          sx={{ color: meta.labelColor || meta.color, flexShrink: 0 }}
        >
          {meta.label}
        </Typography>
      </Stack>
    );
  };

  const renderChecks = (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        maxWidth: PANEL_MAX_WIDTH,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2, py: 1.75 }}
      >
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: summaryIcon.bg,
            color: summaryIcon.color,
          }}
        >
          <Iconify icon={summaryIcon.icon} width={20} />
        </Box>
        <Stack sx={{ flex: 1 }}>
          <Typography
            variant="s1_2"
            fontWeight="fontWeightSemiBold"
            sx={{ color: "text.primary" }}
          >
            Validation checks
          </Typography>
          <Typography variant="s2_1" sx={{ color: "text.secondary" }}>
            {summary}
          </Typography>
        </Stack>
        <IconButton size="small" onClick={() => setExpanded((v) => !v)}>
          <Iconify
            icon={
              expanded
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={18}
          />
        </IconButton>
      </Stack>

      <Collapse in={expanded}>
        {displayChecks.map(renderRow)}

        <Stack
          direction="row"
          alignItems="center"
          justifyContent="center"
          spacing={1}
          onClick={rerunDisabled ? undefined : () => refetch()}
          sx={{
            px: 2,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
            cursor: rerunDisabled ? "default" : "pointer",
            color: rerunDisabled ? "text.disabled" : "text.primary",
            "&:hover": {
              bgcolor: rerunDisabled ? "transparent" : "action.hover",
            },
          }}
        >
          <Iconify icon="solar:refresh-linear" width={16} />
          <Typography variant="s2_1" fontWeight="fontWeightSemiBold">
            Validate requirements
          </Typography>
        </Stack>
      </Collapse>
    </Box>
  );

  return (
    <>
      {renderHead}
      {renderChecks}

      <Stack spacing={0.5} sx={{ maxWidth: PANEL_MAX_WIDTH, mt: 2 }}>
        <LoadingButton
          fullWidth
          color="primary"
          variant="contained"
          onClick={onContinue}
          disabled={blocked}
          sx={{ height: 40, borderRadius: 0.5 }}
        >
          Continue
        </LoadingButton>
        <LoadingButton
          fullWidth
          variant="text"
          onClick={onBack}
          sx={{ height: 34, borderRadius: 0.5, color: "text.secondary" }}
        >
          Back
        </LoadingButton>
      </Stack>
    </>
  );
}

ValidationStep.propTypes = {
  mode: PropTypes.string,
  onBack: PropTypes.func.isRequired,
  onContinue: PropTypes.func.isRequired,
  onProgress: PropTypes.func,
};
