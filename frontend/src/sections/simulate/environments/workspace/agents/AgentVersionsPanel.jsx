import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  canMakeDefault,
  isStuck,
  isVersioningDisabled,
  useDiscardAgentVersion,
  useEnvironmentVersions,
  useMoveDefault,
  useNudges,
  useRetryAgentVersion,
} from "src/api/simulate-environments/versions";
import { useNudgeActions } from "src/api/simulate-environments/nudgeActions";
import {
  AGENT_VERSIONS_COPY as COPY,
  VERSIONS_COPY,
} from "../workspace.constants";
import { NudgeStateLine } from "../../buildEnvironment/console/NudgeCard";
import PendingCommitCard from "../../buildEnvironment/console/PendingCommitCard";
import { refusalText } from "../evals/refusalText";
import MakeDefaultConfirm from "../versions/MakeDefaultConfirm";
import VersionHeader from "../versions/VersionHeader";
import AgentCredentials from "./AgentCredentials";
import AgentSourceChange from "./AgentSourceChange";
import { PreflightReport } from "./PreflightStatus";
import RebuildAction from "./RebuildAction";
import { activationText, named } from "./agentVersionStatus";

// The nudge states a version's row shows with its actions or what it did.
const ACTIONABLE_NUDGE = new Set([
  "pending",
  "queued",
  "running",
  "failed",
  "applied",
  "no_change",
]);

// Only this environment's own changes: its default and the versions it
// descends from have passed or are legacy, so never need a check again.
const canRetry = (version) =>
  version.requestedHere &&
  (version.preflightStatus === "failed" || isStuck(version));
// As the server discards it: never one another environment made, holds or
// descends from.
const canDiscard = (version) => version.discardable;

const HIDDEN = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: 1,
  width: 1,
  margin: -1,
  padding: 0,
  overflow: "hidden",
  position: "absolute",
  whiteSpace: "nowrap",
};

// What to read aloud when a version's check starts or lands.
function useCheckAnnouncement(versions) {
  const seen = useRef(null);
  const [text, setText] = useState("");
  useEffect(() => {
    if (!versions) return;
    const before = seen.current;
    seen.current = Object.fromEntries(
      versions.map((one) => [one.id, one.preflightStatus]),
    );
    if (!before) return;
    const changed = versions.filter(
      (one) =>
        before[one.id] !== one.preflightStatus &&
        COPY.preflight[one.preflightStatus],
    );
    if (changed.length) {
      setText(
        changed
          .map((one) =>
            COPY.checkStatus(named(one), COPY.preflight[one.preflightStatus]),
          )
          .join(". "),
      );
    }
  }, [versions]);
  return text;
}

const errorFor = (mutation, version, fallback) =>
  mutation.isError && mutation.variables?.versionId === version.id
    ? refusalText(mutation.error, fallback)
    : null;

function AgentVersionRow({
  version,
  busy,
  retrying,
  onMakeDefault,
  onDiscard,
  onRetry,
}) {
  const activation = activationText(version);
  const label = named(version);
  return (
    <Box
      data-testid={`agent-version-${version.id}`}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 1.5,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Box flex={1} minWidth={0}>
          <VersionHeader version={version} />
          <PreflightReport report={version.preflightReport} />
          {activation && (
            <Typography
              sx={{ typography: "s3", color: "warning.main", mt: 0.5 }}
            >
              {activation}
            </Typography>
          )}
        </Box>
        <Stack direction="row" spacing={1} flexShrink={0}>
          {canRetry(version) && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={busy}
              onClick={onRetry}
              aria-label={retrying ? undefined : COPY.retry.actionFor(label)}
              sx={{ textTransform: "none" }}
            >
              {retrying ? COPY.retry.pending : COPY.retry.action}
            </Button>
          )}
          {canDiscard(version) && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={busy}
              onClick={onDiscard}
              aria-label={COPY.discard.actionFor(label)}
              sx={{ textTransform: "none" }}
            >
              {COPY.discard.action}
            </Button>
          )}
          {canMakeDefault("agent", version) && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={busy}
              onClick={onMakeDefault}
              aria-label={VERSIONS_COPY.makeDefault.actionFor(label)}
              sx={{ textTransform: "none" }}
            >
              {VERSIONS_COPY.makeDefault.action}
            </Button>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}
AgentVersionRow.propTypes = {
  version: PropTypes.object.isRequired,
  busy: PropTypes.bool,
  retrying: PropTypes.bool,
  onMakeDefault: PropTypes.func.isRequired,
  onDiscard: PropTypes.func.isRequired,
  onRetry: PropTypes.func.isRequired,
};

function DiscardConfirm({ version, pending, onConfirm, onClose }) {
  // Keep naming the version while the dialog fades out.
  const shown = useRef(version);
  if (version) shown.current = version;
  return (
    <ConfirmDialog
      open={Boolean(version)}
      onClose={pending ? undefined : onClose}
      closeDisabled={pending}
      title={COPY.discard.title(named(shown.current))}
      content={
        <Typography component="span" sx={{ typography: "s2" }}>
          {COPY.discard.body}
        </Typography>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="error"
          onClick={onConfirm}
          disabled={pending}
          sx={{ px: 3 }}
        >
          {pending ? COPY.discard.pending : COPY.discard.confirm}
        </Button>
      }
    />
  );
}
DiscardConfirm.propTypes = {
  version: PropTypes.object,
  pending: PropTypes.bool,
  onConfirm: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

// The environment's agent versions on real data: each version's check and
// why it is or isn't the default, the default's credentials, and the actions
// that move the default, set a version aside or check it again, and the
// environment nudge on the version that raised it.
export default function AgentVersionsPanel({ env }) {
  const envId = env?.id;
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, isLoading, error } = useEnvironmentVersions(envId, {
    enabled,
  });
  const move = useMoveDefault(envId);
  const discard = useDiscardAgentVersion(envId);
  const retry = useRetryAgentVersion(envId);
  const { data: nudges } = useNudges(envId, { enabled });
  const nudgeActions = useNudgeActions(envId);
  const [confirming, setConfirming] = useState(null);
  const announcement = useCheckAnnouncement(data?.agent);

  if (!enabled) return null;
  const note = (text, color = "text.secondary") => (
    <Typography sx={{ typography: "s2", color }}>{text}</Typography>
  );
  if (isLoading) return <CircularProgress size={22} sx={{ m: 2.5 }} />;
  if (!data) {
    return (
      <Box sx={{ p: 2.5 }}>
        {isVersioningDisabled(error)
          ? note(COPY.disabled)
          : note(COPY.loadError, "error.main")}
      </Box>
    );
  }

  const versions = data.agent;
  const defaultVersion = data.defaults.agent;
  const busy = move.isPending || discard.isPending || retry.isPending;
  const resetActions = () => {
    move.reset();
    discard.reset();
    retry.reset();
  };
  // The newest environment nudge a version raised: its actions and progress,
  // or that it was skipped.
  const latestNudge = (version) =>
    nudges?.list.find(
      (nudge) =>
        nudge.target === "environment" &&
        nudge.triggerAgentVersion?.id === version.id,
    );
  const close = () => setConfirming(null);
  // The dialog stays mounted while it fades out, after the choice is cleared.
  const confirmMove = () =>
    confirming &&
    move.mutate(
      {
        piece: "agent",
        versionId: confirming.version.id,
        expectedDefaultId: defaultVersion?.id ?? null,
      },
      { onSettled: close },
    );
  const confirmDiscard = () =>
    confirming &&
    discard.mutate({ versionId: confirming.version.id }, { onSettled: close });

  return (
    <Stack spacing={2.5} sx={{ p: 2.5 }}>
      <Box role="status" aria-live="polite" sx={HIDDEN}>
        {announcement}
      </Box>
      <AgentSourceChange
        envId={envId}
        defaultVersion={defaultVersion}
        versions={versions}
      />
      <AgentCredentials
        envId={envId}
        defaultVersion={defaultVersion}
        versions={versions}
      />
      <Box>
        <Typography
          sx={{ typography: "s1", fontWeight: "fontWeightBold", mb: 1 }}
        >
          {COPY.title}
        </Typography>
        {env.pendingCommit && (
          <Box sx={{ mb: 1 }}>
            <PendingCommitCard envId={envId} commit={env.pendingCommit} />
          </Box>
        )}
        {versions.length ? (
          <Stack spacing={1}>
            {versions.map((version) => {
              const failure =
                errorFor(move, version, VERSIONS_COPY.makeDefault.error) ||
                errorFor(discard, version, COPY.discard.error) ||
                errorFor(retry, version, COPY.retry.error);
              const nudge = latestNudge(version);
              const flags = nudge && nudgeActions.flagsFor(nudge.id);
              return (
                <Box key={version.id}>
                  <AgentVersionRow
                    version={version}
                    busy={busy}
                    retrying={
                      retry.isPending &&
                      retry.variables?.versionId === version.id
                    }
                    onRetry={() => {
                      resetActions();
                      retry.mutate({ versionId: version.id });
                    }}
                    onMakeDefault={() => {
                      resetActions();
                      setConfirming({ action: "default", version });
                    }}
                    onDiscard={() => {
                      resetActions();
                      setConfirming({ action: "discard", version });
                    }}
                  />
                  {failure && (
                    <Typography
                      role="alert"
                      sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
                    >
                      {failure}
                    </Typography>
                  )}
                  {ACTIONABLE_NUDGE.has(nudge?.state) ? (
                    <RebuildAction
                      nudge={nudge}
                      label={named(version)}
                      nudgesEnabled={env.versioning?.nudges_enabled !== false}
                      answering={flags.answering}
                      rebuilding={flags.rebuilding}
                      stopping={flags.stopping}
                      focus={flags.settled}
                      error={flags.error}
                      onRebuild={() => nudgeActions.rebuild(nudge.id)}
                      onSkipNudge={() => nudgeActions.skip(nudge.id)}
                      onStopNudge={() => nudgeActions.stop(nudge.id)}
                    />
                  ) : (
                    nudge?.state === "answered_no" && (
                      <NudgeStateLine
                        focus={flags.settled}
                        sx={{ typography: "s3", mt: 0.5 }}
                      >
                        {COPY.nudge.skipped}
                      </NudgeStateLine>
                    )
                  )}
                </Box>
              );
            })}
          </Stack>
        ) : (
          note(COPY.empty)
        )}
      </Box>
      <MakeDefaultConfirm
        piece="agent"
        version={confirming?.action === "default" ? confirming.version : null}
        pending={move.isPending}
        onConfirm={confirmMove}
        onClose={close}
      />
      <DiscardConfirm
        version={confirming?.action === "discard" ? confirming.version : null}
        pending={discard.isPending}
        onConfirm={confirmDiscard}
        onClose={close}
      />
    </Stack>
  );
}

AgentVersionsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({
      enabled: PropTypes.bool,
      nudges_enabled: PropTypes.bool,
    }),
    pendingCommit: PropTypes.shape({
      commitSha: PropTypes.string,
      ref: PropTypes.string,
    }),
  }).isRequired,
};
