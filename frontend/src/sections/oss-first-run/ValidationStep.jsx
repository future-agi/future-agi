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

// While unreachable we poll fast for the first stretch — on a genuine first run
// the containers are up seconds before Django is — then back off so a long
// outage doesn't hammer the box.
const FAST_POLL_MS = 2000;
const SLOW_POLL_MS = 10000;
const FAST_POLL_WINDOW_MS = 30000;

const PANEL_MAX_WIDTH = 460;

const STATUS_META = {
  [PASSED]: {
    icon: "solar:check-circle-bold",
    color: "success.main",
    label: "Validated",
  },
  [WARNING]: {
    icon: "solar:danger-triangle-bold",
    color: "warning.main",
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
    icon: "solar:record-linear",
    color: "text.disabled",
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

  const { data, isError, isFetching, refetch, errorUpdatedAt } = useSetupChecks(
    mode,
    { refetchInterval: pollInterval },
  );

  const checks = useMemo(() => data?.checks ?? [], [data]);

  let connectionState = CONNECTION_STATE.CONNECTING;
  if (isError) connectionState = CONNECTION_STATE.UNREACHABLE;
  else if (data) connectionState = CONNECTION_STATE.REACHABLE;
  const reachable = connectionState === CONNECTION_STATE.REACHABLE;

  // Back off polling after the first stretch of being unreachable, and stop
  // entirely once we get a snapshot — from then on, runs are user-initiated.
  //
  // Keyed on `errorUpdatedAt` rather than `isError`: consecutive failures leave
  // isError identical, so the effect would never re-run and the backoff would
  // never engage. Each new error bumps the timestamp.
  useEffect(() => {
    if (reachable) {
      unreachableSince.current = null;
      setPollInterval(false);
      return;
    }
    if (unreachableSince.current === null) {
      unreachableSince.current = performance.now();
    }
    const elapsed = performance.now() - unreachableSince.current;
    setPollInterval(
      elapsed > FAST_POLL_WINDOW_MS ? SLOW_POLL_MS : FAST_POLL_MS,
    );
  }, [reachable, errorUpdatedAt]);

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  // Stagger is a reveal animation over ONE response, not one request per row.
  useEffect(() => {
    clearTimers();
    setRevealCount(0);
    if (!checks.length) return undefined;
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

  const revealed = checks.slice(0, revealCount);
  const stillRevealing = revealCount < checks.length;

  const counts = useMemo(() => {
    const c = { passed: 0, warning: 0, failed: 0, optional: 0 };
    revealed.forEach((check) => {
      if (check.status === PASSED) c.passed += 1;
      else if (check.status === WARNING) c.warning += 1;
      else if (check.status === FAILED) c.failed += 1;
      else if (check.status === SKIPPED) c.optional += 1;
    });
    return c;
  }, [revealed]);

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

  const tint = (key, opacity = 0.16) => alpha(theme.palette[key].main, opacity);

  // Connection state and check state are separate axes. An unreachable server
  // shows a spinner here, never a wall of failed checks.
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
      color: "warning.main",
      bg: tint("warning"),
    };
  } else if (reachable && !stillRevealing) {
    summaryIcon = {
      icon: "solar:check-circle-bold",
      color: "success.main",
      bg: tint("success"),
    };
  }

  // Only a REQUIRED check that FAILED blocks. `required` is computed server-side
  // per launch mode — never re-derived here, so the two cannot disagree.
  const blocked =
    !reachable ||
    isFetching ||
    stillRevealing ||
    checks.some((c) => c.required && c.status === FAILED);

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
        Validation runs immediately. You can re-run the checks at any time. If
        you get stuck, see the{" "}
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

        <Typography
          variant="s2_1"
          fontWeight="fontWeightSemiBold"
          sx={{ color: meta.color, flexShrink: 0 }}
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
        {/* Rows fill the remaining viewport height, so the list stretches on
            tall screens and scrolls on short ones — while Continue / Back stay
            visible. */}
        <Box sx={{ maxHeight: "calc(100vh - 560px)", overflowY: "auto" }}>
          {revealed.map(renderRow)}
        </Box>

        {/* Re-runs everything: one request returns the whole snapshot, so a
            per-row re-run would be a lie about independent probing. */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="center"
          spacing={1}
          onClick={isFetching ? undefined : () => refetch()}
          sx={{
            px: 2,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
            cursor: isFetching ? "default" : "pointer",
            color: isFetching ? "text.disabled" : "text.primary",
            "&:hover": { bgcolor: isFetching ? "transparent" : "action.hover" },
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
