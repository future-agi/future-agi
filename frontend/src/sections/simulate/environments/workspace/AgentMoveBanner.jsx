import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import {
  isVersioningDisabled,
  movingAgent,
  useEnvironmentVersions,
} from "src/api/simulate-environments/versions";
import { activationText, named } from "./agents/agentVersionStatus";
import { AGENT_MOVE_COPY as COPY } from "./workspace.constants";

const LONG_CHECK_MS = 20 * 60 * 1000;
// Ends that stay true until someone acts, so they show even when the page opened after the check ended.
const LASTING = new Set(["pair_failed", "validation_retry"]);
// An end that only means a later move took over: nothing to tell.
const QUIET = new Set(["superseded"]);
const DISMISSED_KEY = "agent-move-dismissed";
const NONE = [];

const readDismissed = () => {
  try {
    return JSON.parse(window.sessionStorage.getItem(DISMISSED_KEY)) || [];
  } catch {
    return [];
  }
};

const writeDismissed = (list) => {
  try {
    window.sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(list));
  } catch {
    // Storage blocked: the dismissal lasts until the page reloads.
  }
};

// The move that ended in a way the user must know about: one this page watched end, or a lasting one on an agent
// version newer than the default.
const endedMove = (agents, watchedEnd) => {
  if (watchedEnd) {
    const version = agents.find((one) => one.id === watchedEnd);
    if (version && !version.isDefault && activationText(version))
      return version;
  }
  const defaultAt = agents.findIndex((one) => one.isDefault);
  const newer = defaultAt === -1 ? agents : agents.slice(0, defaultAt);
  const latest = newer.find((one) => one.status === "active");
  return latest && LASTING.has(latest.activationOutcome) ? latest : null;
};

const rowSx = (tone) => ({
  px: 3,
  py: 1.25,
  bgcolor: `status.${tone}.bg`,
  borderBottom: "1px solid",
  borderColor: "divider",
});

const dotSx = {
  width: 8,
  height: 8,
  borderRadius: "50%",
  bgcolor: "status.info.solid",
  flexShrink: 0,
  animation: "agent-move-pulse 1.4s ease-in-out infinite",
  "@keyframes agent-move-pulse": {
    "0%,100%": { opacity: 0.4 },
    "50%": { opacity: 1 },
  },
  "@media (prefers-reduced-motion: reduce)": { animation: "none" },
};

/**
 * Under the version bar: a new default agent being checked with this environment, and, once the check ends, a toast
 * when it became the default or a line saying why it didn't. Reads only the versions list, which is asked again
 * while the move waits.
 */
export default function AgentMoveBanner({ env, onOpenAgentVersions }) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, error } = useEnvironmentVersions(env?.id, { enabled });
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const shown = enabled && Boolean(data) && !isVersioningDisabled(error);
  const agents = shown ? data.agent : NONE;
  const moving = movingAgent(agents);
  const movingId = moving?.id ?? null;

  // The move this page saw waiting, and when it first saw it.
  const watched = useRef(null);
  const [watchedEnd, setWatchedEnd] = useState(null);
  const [slow, setSlow] = useState(false);
  const [dismissed, setDismissed] = useState(readDismissed);

  useEffect(() => {
    if (!shown) return;
    if (movingId) {
      if (watched.current?.id !== movingId) {
        watched.current = { id: movingId, firstSeen: Date.now() };
        setSlow(false);
        setWatchedEnd(null);
        // A new try forgets an old dismissal, so the same failure shows again.
        const tried = `${env?.id}:${movingId}:`;
        setDismissed((list) => {
          const next = list.filter((key) => !key.startsWith(tried));
          if (next.length === list.length) return list;
          writeDismissed(next);
          return next;
        });
      }
      return;
    }
    const was = watched.current;
    if (!was) return;
    watched.current = null;
    setSlow(false);
    const version = agents.find((one) => one.id === was.id);
    if (!version) return;
    if (version.isDefault) {
      enqueueSnackbar(COPY.nowDefault(named(version)), { variant: "success" });
    } else if (!QUIET.has(version.activationOutcome)) {
      setWatchedEnd(version.id);
    }
  }, [shown, movingId, agents, env?.id]);

  // shortcut: the server sends no start time for the move's check, so "long" counts from when this page first saw
  // it; use the check's start once the API sends one.
  useEffect(() => {
    if (!movingId || !watched.current) return undefined;
    const left = watched.current.firstSeen + LONG_CHECK_MS - Date.now();
    if (left <= 0) {
      setSlow(true);
      return undefined;
    }
    const timer = setTimeout(() => setSlow(true), left);
    return () => clearTimeout(timer);
  }, [movingId]);

  const ended = moving ? null : endedMove(agents, watchedEnd);
  const endedKey = ended
    ? `${env?.id}:${ended.id}:${ended.activationOutcome}`
    : null;
  const failure = ended && !dismissed.includes(endedKey) ? ended : null;
  const dismiss = () => {
    const next = [...dismissed, endedKey];
    setDismissed(next);
    writeDismissed(next);
  };

  let content = null;
  if (moving) {
    const current = data.defaults.agent;
    content = (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={rowSx("info")}
      >
        <Box sx={dotSx} />
        <Typography
          sx={{
            typography: "s1",
            fontWeight: "fontWeightMedium",
            color: "status.info.fg",
            flexShrink: 0,
          }}
        >
          {COPY.checking(named(moving))}
        </Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary", flex: 1 }}>
          {[current && COPY.stays(named(current)), slow && COPY.slow]
            .filter(Boolean)
            .join(" ")}
        </Typography>
        {slow && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onOpenAgentVersions}
          >
            {COPY.open}
          </Button>
        )}
      </Stack>
    );
  } else if (failure) {
    content = (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={rowSx("warning")}
      >
        <Iconify
          icon="solar:danger-triangle-linear"
          width={16}
          sx={{ color: "status.warning.fg", flexShrink: 0 }}
        />
        <Typography
          sx={{ typography: "s1", color: "status.warning.fg", flex: 1 }}
        >
          {COPY.ended(named(failure), activationText(failure))}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onOpenAgentVersions}
        >
          {COPY.open}
        </Button>
        <IconButton size="small" aria-label={COPY.dismiss} onClick={dismiss}>
          <Iconify icon="solar:close-circle-linear" width={16} />
        </IconButton>
      </Stack>
    );
  }

  // Keeps the last row during the close, so it doesn't shrink empty.
  const last = useRef(null);
  if (content) last.current = content;
  return (
    <Collapse
      in={Boolean(content)}
      timeout={reduceMotion ? 0 : "auto"}
      unmountOnExit
    >
      <Box role="status" aria-live="polite">
        {content ?? last.current}
      </Box>
    </Collapse>
  );
}

AgentMoveBanner.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }),
  // Opens the agent versions drawer, where the move's reason and its retry live.
  onOpenAgentVersions: PropTypes.func,
};
