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
  Divider,
  IconButton,
  ButtonBase,
  useTheme,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ROW_HEIGHT } from "src/theme/sizes";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { statusColors } from "src/utils/runStatus";
import { useSetupChecks } from "src/api/ossSetup/oss-setup";
import {
  CHECK_STATUS,
  CHECK_REVEAL_STAGGER_MS,
  CONNECTION_STATE,
  LAUNCH_MODE,
  getSetupMeta,
  nextSteps,
} from "./constants";

const { PENDING, PASSED, WARNING, FAILED, SKIPPED } = CHECK_STATUS;

// Fast while the stack boots, then back off so a long outage doesn't hammer it.
const FAST_POLL_MS = 2000;
const SLOW_POLL_MS = 10000;
const FAST_POLL_WINDOW_MS = 30000;

// The server caches a snapshot for 3s, so anything faster is wasted.
const UNSETTLED_POLL_MS = 5000;

// A cached snapshot comes back in milliseconds, so a spinner tied purely to the
// request would flash and read as a dead button. Hold it for one full turn.
const REVALIDATE_MIN_SPIN_MS = 900;

// How long a snapshot may go unchanged before polling gives up and leaves it
// to the re-run controls.
const STALL_TIMEOUT_MS = 60000;

const PANEL_MAX_WIDTH = 460;

// The list scrolls inside the card past this many rows, so the page doesn't.
// The half row peeking out says there is more below.
const VISIBLE_ROWS = 4.5;

const SUPPORT_EMAIL = "support@futureagi.com";

// A `fix` string (and a next step) carries inline code spans and nothing else,
// so a markdown dependency would be overkill.
function renderInlineCode(text) {
  return String(text)
    .split("`")
    .map((part, i) =>
      i % 2 === 1 ? (
        <Box
          key={`${i}:${part}`}
          component="code"
          sx={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: "0.92em",
            color: "text.primary",
            bgcolor: "background.neutral",
            px: 0.6,
            py: 0.15,
            borderRadius: "4px",
          }}
        >
          {part}
        </Box>
      ) : (
        part
      ),
    );
}

const STATUS_META = {
  [PASSED]: {
    icon: "solar:check-circle-bold",
    role: "success",
    label: "Ready",
  },
  [WARNING]: {
    icon: "solar:danger-triangle-bold",
    role: "warning",
    label: "Caution",
  },
  [FAILED]: {
    icon: "solar:close-circle-bold",
    role: "danger",
    label: "Failed",
  },
  [SKIPPED]: {
    icon: "solar:minus-circle-linear",
    role: "neutral",
    label: "Optional",
  },
  [PENDING]: {
    icon: "svg-spinners:90-ring",
    role: "info",
    label: "Checking…",
  },
};

export default function ValidationStep({
  mode,
  onSwitchMode,
  onContinue,
  onProgress,
  authenticated = false,
}) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [revealCount, setRevealCount] = useState(0);
  const [pollInterval, setPollInterval] = useState(FAST_POLL_MS);
  const [revalidating, setRevalidating] = useState(false);
  const unreachableSince = useRef(null);
  const timers = useRef([]);
  const hasStaggered = useRef(false);
  const lastSignature = useRef(null);
  const stalledSince = useRef(null);
  const spinTimer = useRef(null);
  const rowsRef = useRef(null);

  const { data, isError, refetch, errorUpdatedAt } = useSetupChecks(mode, {
    refetchInterval: pollInterval,
  });

  const checks = useMemo(() => data?.checks ?? [], [data]);
  const setupMeta = getSetupMeta(data?.setup);
  const collectorUrl = data?.collector_http_url;
  const accountExists = Boolean(data?.account_exists);
  const steps = useMemo(
    () => nextSteps({ authenticated, accountExists, collectorUrl }),
    [authenticated, accountExists, collectorUrl],
  );

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

  const handleRevalidate = useCallback(() => {
    setRevalidating(true);
    const startedAt = performance.now();
    const stopSpinning = () => {
      const elapsed = performance.now() - startedAt;
      spinTimer.current = setTimeout(
        () => setRevalidating(false),
        Math.max(0, REVALIDATE_MIN_SPIN_MS - elapsed),
      );
    };
    refetch().then(stopSpinning, stopSpinning);
  }, [refetch]);

  useEffect(() => () => clearTimeout(spinTimer.current), []);

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

  // Every row shows while the checks reveal. After that the passing ones fold
  // into the summary line, leaving only what needs a look (or is optional);
  // expanding adds them underneath, so those rows stay where they were.
  const showAll = expanded || stillRevealing;
  const visibleChecks = useMemo(() => {
    if (stillRevealing) return displayChecks;
    const rest = displayChecks.filter((check) => check.status !== PASSED);
    if (!expanded) return rest;
    return [
      ...rest,
      ...displayChecks.filter((check) => check.status === PASSED),
    ];
  }, [displayChecks, expanded, stillRevealing]);

  // Follow the newest row inside the card only: scrollIntoView would move the
  // page too.
  useEffect(() => {
    const list = rowsRef.current;
    const row = list?.children[revealCount - 1];
    if (!stillRevealing || !row) return;
    list.scrollTop = Math.max(
      0,
      row.offsetTop + row.offsetHeight - list.clientHeight,
    );
  }, [revealCount, stillRevealing]);

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
      return "Waiting for your instance to power up. Normal on a first run.";
    }
    const parts = [];
    if (counts.passed) parts.push(`${counts.passed} ready`);
    if (counts.warning) parts.push(`${counts.warning} caution`);
    if (counts.failed) parts.push(`${counts.failed} failed`);
    if (counts.optional) parts.push(`${counts.optional} optional`);
    return parts.join(" · ") || "Running pre-flight…";
  }, [counts, reachable]);

  // One line per check that is actually down, warnings included: on a test
  // flight a stopped minio comes back WARNING and its uploads still fail, so
  // the operator needs the same remedy. The row itself already says what
  // breaks, so this stays a single sentence.
  const needsFix = useMemo(
    () =>
      checks.filter(
        (c) => (c.status === FAILED || c.status === WARNING) && c.fix,
      ),
    [checks],
  );

  const supportHref = useMemo(() => {
    const ids = checks
      .filter((c) => c.status === FAILED)
      .map((c) => c.id)
      .join(", ");
    const subject = `Self-hosted pre-flight: ${ids || "need a hand"}`;
    const body = `Launch mode: ${mode}\nFailed checks: ${ids || "none"}\n\nWhat I have already tried:\n`;
    // mailto, not Gmail's web compose: that sends anyone on Outlook, Fastmail
    // or a corporate client to a Google sign-in page instead of a draft.
    return (
      `mailto:${SUPPORT_EMAIL}` +
      `?subject=${encodeURIComponent(subject)}` +
      `&body=${encodeURIComponent(body)}`
    );
  }, [checks, mode]);

  const roleColors = (role) => statusColors(theme, role);

  // An unreachable server shows a spinner, never a wall of failed checks.
  let summaryIcon = {
    icon: "svg-spinners:90-ring-with-bg",
    color: "text.secondary",
    bg: theme.palette.action.selected,
  };
  if (reachable && counts.failed) {
    summaryIcon = {
      icon: "solar:close-circle-bold",
      color: roleColors("danger").fg,
      bg: roleColors("danger").bg,
    };
  } else if (reachable && counts.warning) {
    summaryIcon = {
      icon: "solar:danger-triangle-bold",
      color: roleColors("warning").fg,
      bg: roleColors("warning").bg,
    };
  } else if (reachable) {
    summaryIcon = {
      icon: "solar:check-circle-bold",
      color: roleColors("success").fg,
      bg: roleColors("success").bg,
    };
  }

  // Only live mode blocks on results, and only on FAILED: in that mode the
  // server downgrades every non-required check to a warning, so this is exactly
  // its required set.
  const hasFailure = useMemo(
    () => checks.some((check) => check.status === FAILED),
    [checks],
  );
  const blockedByFailure = mode === LAUNCH_MODE.LIVE && hasFailure;

  const blocked =
    !reachable || !checks.length || stillRevealing || blockedByFailure;

  const rerunDisabled = stillRevealing || revalidating;

  const spinSx = {
    "@keyframes revalidateSpin": { to: { transform: "rotate(360deg)" } },
    animation: revalidating ? "revalidateSpin 0.8s linear infinite" : "none",
  };

  const renderHead = (
    <Stack sx={{ mb: 2 }}>
      <Typography
        variant="l2"
        component="h1"
        fontWeight="fontWeightSemiBold"
        sx={{ color: "text.primary" }}
      >
        Pre-flight checks
      </Typography>
      <Typography
        variant="s1_2"
        sx={{ color: "text.secondary", maxWidth: PANEL_MAX_WIDTH, mt: 0.75 }}
      >
        We&apos;re running through your onboard systems now. Re-run anything
        that needs attention. The{" "}
        <Link
          href="https://docs.futureagi.com/docs/self-hosting"
          target="_blank"
          rel="noopener"
          underline="always"
        >
          self-host docs
        </Link>{" "}
        are your co-pilot if a check needs a hand.
      </Typography>
      {setupMeta && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="flex-start"
          sx={{ mt: 1.5, maxWidth: PANEL_MAX_WIDTH }}
          data-testid="oss-setup-kind"
        >
          <Iconify
            icon={setupMeta.icon}
            width={18}
            sx={{ color: "primary.main", flexShrink: 0, mt: "1px" }}
          />
          <Typography variant="s2_1" sx={{ color: "text.secondary" }}>
            <Box
              component="span"
              sx={{ color: "text.primary", fontWeight: "fontWeightSemiBold" }}
            >
              {setupMeta.title}.
            </Box>{" "}
            {setupMeta.description}
          </Typography>
        </Stack>
      )}
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
          px: 1.5,
          py: 0.5,
          minHeight: check.detail ? ROW_HEIGHT.twoLine : ROW_HEIGHT.default,
          borderTop: "1px solid",
          borderColor: failed ? roleColors("danger").border : "border.subtle",
          bgcolor: failed ? roleColors("danger").bg : "transparent",
        }}
      >
        <Box
          sx={{
            width: 20,
            display: "flex",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon={meta.icon}
            width={16}
            sx={{ color: roleColors(meta.role).solid }}
          />
        </Box>

        <Stack sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="s2_1"
            fontWeight="fontWeightMedium"
            sx={{ color: "text.primary" }}
          >
            {check.label}
          </Typography>
          {check.detail && (
            <Typography variant="s2" sx={{ color: "text.tertiary" }}>
              {check.detail}
            </Typography>
          )}
        </Stack>

        {canRerun && (
          <CustomTooltip show title="Re-run pre-flight" size="small" arrow>
            <span>
              <IconButton
                size="small"
                aria-label="Re-run pre-flight"
                disabled={rerunDisabled}
                onClick={handleRevalidate}
                sx={{ color: "text.primary", flexShrink: 0 }}
              >
                <Iconify icon="solar:refresh-linear" width={16} sx={spinSx} />
              </IconButton>
            </span>
          </CustomTooltip>
        )}

        <StatusBadge
          status={check.status}
          label={meta.label}
          role={meta.role}
          dot={false}
        />
      </Stack>
    );
  };

  const renderChecks = (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px",
        overflow: "hidden",
        maxWidth: PANEL_MAX_WIDTH,
      }}
    >
      <ButtonBase
        aria-expanded={showAll}
        disabled={stillRevealing || !checks.length}
        onClick={() => setExpanded((v) => !v)}
        sx={{
          width: "100%",
          gap: 1.5,
          px: 1.5,
          py: 1.25,
          justifyContent: "flex-start",
          textAlign: "left",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: summaryIcon.bg,
            color: summaryIcon.color,
          }}
        >
          <Iconify icon={summaryIcon.icon} width={16} />
        </Box>
        <Stack sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="s1_2"
            fontWeight="fontWeightSemiBold"
            sx={{ color: "text.primary" }}
          >
            Onboard systems
          </Typography>
          <Typography variant="s2_1" sx={{ color: "text.secondary" }}>
            {summary}
          </Typography>
        </Stack>
        {checks.length > 0 && (
          <Iconify
            icon={
              showAll
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={20}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
      </ButtonBase>

      <Box
        ref={rowsRef}
        sx={{
          position: "relative",
          maxHeight: ROW_HEIGHT.default * VISIBLE_ROWS,
          overflowY: "auto",
        }}
      >
        {visibleChecks.map(renderRow)}
      </Box>

      {expanded && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="center"
          spacing={1}
          onClick={rerunDisabled ? undefined : handleRevalidate}
          sx={{
            px: 1.5,
            py: 1,
            borderTop: "1px solid",
            borderColor: "border.subtle",
            cursor: rerunDisabled ? "default" : "pointer",
            color: rerunDisabled ? "text.disabled" : "text.primary",
            userSelect: "none",
            transition: "background-color 0.2s ease",
            "&:hover": {
              bgcolor: rerunDisabled ? "transparent" : "action.hover",
            },
          }}
        >
          <Iconify icon="solar:refresh-linear" width={16} sx={spinSx} />
          <Typography variant="s2_1" fontWeight="fontWeightSemiBold">
            {revalidating ? "Re-running…" : "Re-run pre-flight"}
          </Typography>
        </Stack>
      )}
    </Box>
  );

  const renderDetails = needsFix.length > 0 && !stillRevealing && (
    <Box
      sx={{
        maxWidth: PANEL_MAX_WIDTH,
        mt: 1.25,
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px",
      }}
    >
      <Typography
        variant="s2_1"
        fontWeight="fontWeightSemiBold"
        sx={{
          display: "block",
          px: 1.5,
          pt: 1.5,
          color: "text.tertiary",
        }}
      >
        Details
      </Typography>
      {needsFix.map((check, i) => (
        <Stack
          key={check.id}
          direction="row"
          spacing={1.25}
          sx={{
            px: 1.5,
            py: 1,
            borderTop: i === 0 ? "none" : "1px solid",
            borderColor: "border.subtle",
          }}
        >
          <Link
            href={check.docs_url || undefined}
            target="_blank"
            rel="noopener"
            variant="s2_2"
            fontWeight="fontWeightSemiBold"
            sx={{ width: 150, flexShrink: 0, color: "text.primary" }}
          >
            {check.label}
          </Link>
          <Typography variant="s2_1" sx={{ color: "text.secondary" }}>
            {renderInlineCode(check.fix)}
          </Typography>
        </Stack>
      ))}
    </Box>
  );

  // Plain-language next moves, once nothing stands between the operator and
  // Continue.
  const renderNextSteps = !blocked && (
    <Box
      sx={{
        maxWidth: PANEL_MAX_WIDTH,
        mt: 1.25,
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px",
        px: 1.5,
        py: 1.5,
      }}
    >
      <Typography
        variant="s2_1"
        fontWeight="fontWeightSemiBold"
        sx={{
          display: "block",
          color: "text.tertiary",
        }}
      >
        Next up
      </Typography>
      <Stack component="ol" spacing={1} sx={{ m: 0, mt: 1, p: 0 }}>
        {steps.map((next) => (
          <Stack
            key={next.id}
            component="li"
            direction="row"
            spacing={1.25}
            alignItems="flex-start"
            sx={{ listStyle: "none" }}
          >
            <Iconify
              icon={next.icon}
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
            />
            <Typography variant="s2_1" sx={{ color: "text.secondary" }}>
              {renderInlineCode(next.text)}
              {next.link && (
                <>
                  {" "}
                  <Link
                    href={next.link.href}
                    target="_blank"
                    rel="noopener"
                    underline="always"
                  >
                    {next.link.label}
                  </Link>
                </>
              )}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );

  return (
    <>
      {renderHead}
      {renderChecks}
      {renderDetails}
      {renderNextSteps}

      <Stack spacing={0.5} sx={{ maxWidth: PANEL_MAX_WIDTH, mt: 2 }}>
        <LoadingButton
          fullWidth
          color="primary"
          variant="contained"
          onClick={() => onContinue({ accountExists })}
          disabled={blocked}
        >
          Continue
        </LoadingButton>
        {blockedByFailure && !stillRevealing && (
          <>
            <Typography
              variant="s2_1"
              sx={{
                color: roleColors("danger").fg,
                textAlign: "center",
                pt: 0.5,
              }}
            >
              Fix the failed systems to continue with a production launch.
            </Typography>
            <Divider sx={{ my: 1, color: "text.disabled" }}>
              <Typography variant="s2_1">or</Typography>
            </Divider>
            <LoadingButton
              fullWidth
              variant="outlined"
              onClick={() => onSwitchMode(LAUNCH_MODE.EXPERIMENT)}
            >
              Continue with Test flight
            </LoadingButton>
          </>
        )}
        {/* Once a mode is picked the only moves are forward: there is no Back. */}
        {mode === LAUNCH_MODE.EXPERIMENT && (
          <LoadingButton
            fullWidth
            variant="text"
            onClick={() => onSwitchMode(LAUNCH_MODE.LIVE)}
            sx={{ color: "text.secondary" }}
          >
            Switch to Production
          </LoadingButton>
        )}
        <LoadingButton
          fullWidth
          variant="text"
          href={supportHref}
          target="_blank"
          rel="noopener"
          sx={{ color: "text.secondary" }}
        >
          Contact support
        </LoadingButton>
      </Stack>
    </>
  );
}

ValidationStep.propTypes = {
  mode: PropTypes.string,
  onSwitchMode: PropTypes.func.isRequired,
  onContinue: PropTypes.func.isRequired,
  onProgress: PropTypes.func,
  authenticated: PropTypes.bool,
};
