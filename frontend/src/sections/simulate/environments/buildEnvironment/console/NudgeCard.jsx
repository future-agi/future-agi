import PropTypes from "prop-types";
import { useEffect, useId, useRef } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";

import { NUDGE_ACTIVE_STATES } from "src/api/simulate-environments/versions";
import { refusalText } from "../../workspace/evals/refusalText";
import { named } from "../../workspace/agents/agentVersionStatus";
import { changeCount } from "../../workspace/drafts/changeCount";
import VersionDiff from "../../workspace/versions/VersionDiff";
import { isEmptyDiff } from "../../workspace/versions/isEmptyDiff";
import { NUDGE_COPY as COPY } from "../build.constants";

const MONO = "ui-monospace, Menlo, monospace";
const FILES_SHOWN = 8;
const SHORT_SHA = 7;

const short = (sha) => (sha ? String(sha).slice(0, SHORT_SHA) : "…");

// A failed rebuild can run again, unless its environment is gone.
const canRebuildAgain = (nudge) =>
  nudge.state === "failed" && nudge.error?.code !== "environment_deleted";

const answerErrorText = (error, fallback = COPY.skipError) => {
  if (error?.statusCode === 402) return refusalText(error, COPY.usageError);
  return COPY.errors[error?.error] || refusalText(error, fallback);
};

function sourceLine(source, reversed) {
  if (!source?.changed) return null;
  if (source.kind === "github") {
    return reversed
      ? COPY.source.rolledBack(short(source.to))
      : COPY.source.commit(short(source.from), short(source.to));
  }
  return COPY.source[source.kind] || COPY.source.other;
}

function failureText(error) {
  if (!error?.code) return COPY.states.failed;
  return COPY.failures[error.code] || error.message || COPY.failed;
}

function stateText(nudge) {
  const { summary } = nudge;
  if (nudge.state === "queued" && nudge.queuedBehindInput) {
    return COPY.queuedBehindInput;
  }
  if (nudge.state === "applied" && summary?.label) {
    return COPY.applied(summary.piece || nudge.target, summary.label);
  }
  if (nudge.state === "no_change" && summary?.line) return summary.line;
  if (nudge.state === "failed") return failureText(nudge.error);
  if (nudge.state !== "replaced")
    return COPY.states[nudge.state] || nudge.state;
  if (nudge.error?.code === "stopped_by_newer") {
    return COPY.failures.stopped_by_newer;
  }
  const by = nudge.replacedBy;
  if (!by?.triggerLabel) return COPY.replaced;
  if (nudge.target === "suite")
    return COPY.replacedByEnvironment(by.triggerLabel);
  return by.triggerLabel === nudge.triggerAgentVersion?.label
    ? COPY.replacedSameTrigger
    : COPY.replacedByAgent(by.triggerLabel);
}

const heading = (nudge) => {
  if (nudge.target === "suite") {
    return COPY.suiteChanged(named(nudge.baseEnvironmentVersion));
  }
  const label = named(nudge.triggerAgentVersion);
  return nudge.facts.agentDefaultMoved === false
    ? COPY.available(label)
    : COPY.changed(label);
};

const Line = ({ children, color = "text.secondary", mono }) => (
  <Typography
    sx={{ typography: "s3", color, ...(mono && { fontFamily: MONO }) }}
  >
    {children}
  </Typography>
);
Line.propTypes = {
  children: PropTypes.node,
  color: PropTypes.string,
  mono: PropTypes.bool,
};

function Files({ facts }) {
  const files = facts.changedFiles || [];
  if (facts.filesUnavailable) {
    const reason =
      COPY.unavailableReasons[facts.filesUnavailable] || facts.filesUnavailable;
    return <Line>{COPY.filesUnavailable(reason)}</Line>;
  }
  if (!files.length) return null;
  const rest = files.length - FILES_SHOWN;
  return (
    <Box component="ul" sx={{ m: 0, pl: 2 }}>
      {files.slice(0, FILES_SHOWN).map((file) => (
        <Box component="li" key={`${file.previousPath || ""}:${file.path}`}>
          <Line mono color="text.primary">
            {file.path} · {file.status}
            {file.additions != null && ` +${file.additions}`}
            {file.deletions != null && ` -${file.deletions}`}
          </Line>
        </Box>
      ))}
      {rest > 0 && (
        <Box component="li">
          <Line>{COPY.filesMore(rest)}</Line>
        </Box>
      )}
      {facts.filesTruncated && (
        <Box component="li">
          <Line>{COPY.filesTruncated}</Line>
        </Box>
      )}
    </Box>
  );
}
Files.propTypes = { facts: PropTypes.object.isRequired };

function Variables({ variables }) {
  const groups = ["added", "removed", "changed", "unknown"].filter(
    (group) => variables?.[group]?.length,
  );
  return groups.map((group) => (
    <Line key={group}>
      {COPY.variables[group]}: {variables[group].join(", ")}
    </Line>
  ));
}
Variables.propTypes = { variables: PropTypes.object };

// What changed, names only: the source line, files, prompt, variables and
// settings, or the environment's summary for a suite nudge.
export function NudgeFacts({ nudge }) {
  const { facts } = nudge;
  if (nudge.target === "suite") {
    return facts.environmentSummary ? (
      <Line>{facts.environmentSummary}</Line>
    ) : null;
  }
  const source = sourceLine(facts.source, facts.reversed);
  return (
    <Stack spacing={0.5}>
      {source && <Line>{source}</Line>}
      <Files facts={facts} />
      {facts.promptChanged && <Line>{COPY.promptChanged}</Line>}
      <Variables variables={facts.variables} />
      {facts.settingsChanged?.length > 0 && (
        <Line>{COPY.settingsChanged(facts.settingsChanged)}</Line>
      )}
    </Stack>
  );
}
NudgeFacts.propTypes = { nudge: PropTypes.object.isRequired };

// The buttons for a nudge's state, shared by the chat card and the Agents
// panel: Rebuild and Skip while pending, Stop while queued or running, and
// Rebuild again once failed. A button that can't be pressed says why in
// plain text.
export function NudgeActions({
  nudge,
  subject,
  nudgesEnabled = true,
  answering = false,
  rebuilding = false,
  stopping = false,
  error,
  onRebuild,
  onSkipNudge,
  onStopNudge,
}) {
  const reasonId = useId();
  const state = nudge?.state || "pending";
  const pending = state === "pending";
  const again = Boolean(nudge) && canRebuildAgain(nudge);
  const blocked = (pending || again) && !nudgesEnabled ? COPY.rebuildOff : null;
  let rebuildText = again ? COPY.rebuildAgain : COPY.rebuild;
  if (answering && rebuilding) rebuildText = COPY.rebuilding;
  let errorFallback = COPY.skipError;
  if (rebuilding) errorFallback = COPY.rebuildError;
  if (NUDGE_ACTIVE_STATES.has(state)) errorFallback = COPY.stopError;
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" spacing={1}>
        {(pending || again) && (
          <Button
            size="small"
            variant="contained"
            disabled={Boolean(blocked) || answering || !onRebuild}
            onClick={onRebuild}
            aria-label={
              answering ? undefined : COPY.rebuildFor(rebuildText, subject)
            }
            aria-describedby={blocked ? reasonId : undefined}
            sx={{ textTransform: "none" }}
          >
            {rebuildText}
          </Button>
        )}
        {pending && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={answering || !onSkipNudge}
            onClick={onSkipNudge}
            aria-label={answering ? undefined : COPY.skipFor(subject)}
            sx={{ textTransform: "none" }}
          >
            {answering && !rebuilding ? COPY.skipping : COPY.skip}
          </Button>
        )}
        {NUDGE_ACTIVE_STATES.has(state) && nudge?.canStop !== false && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={stopping || !onStopNudge}
            onClick={onStopNudge}
            aria-label={stopping ? undefined : COPY.stopFor(subject)}
            sx={{ textTransform: "none" }}
          >
            {stopping ? COPY.stopping : COPY.stop}
          </Button>
        )}
      </Stack>
      {blocked && (
        <Typography
          id={reasonId}
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          {blocked}
        </Typography>
      )}
      {error && (
        <Typography role="alert" sx={{ typography: "s3", color: "error.main" }}>
          {answerErrorText(error, errorFallback)}
        </Typography>
      )}
    </Stack>
  );
}
NudgeActions.propTypes = {
  nudge: PropTypes.shape({
    state: PropTypes.string,
    canStop: PropTypes.bool,
    error: PropTypes.object,
  }),
  subject: PropTypes.string.isRequired,
  nudgesEnabled: PropTypes.bool,
  answering: PropTypes.bool,
  // The answer in flight is "yes".
  rebuilding: PropTypes.bool,
  stopping: PropTypes.bool,
  error: PropTypes.object,
  onRebuild: PropTypes.func,
  onSkipNudge: PropTypes.func,
  onStopNudge: PropTypes.func,
};

// What a Rebuild does with the piece's unsaved edits: takes them along, or
// sets them aside when they started from an older version.
export function DraftNotice({ draft }) {
  if (!draft) return null;
  return (
    <Line color="text.primary">
      {draft.stale
        ? COPY.draftStale(draft.parentLabel)
        : COPY.draftLive(changeCount(draft.changes), draft.lastEditedBy)}
    </Line>
  );
}
DraftNotice.propTypes = { draft: PropTypes.object };

function Bucket({ title, children }) {
  return (
    <Box component="details" sx={{ "& > summary": { cursor: "pointer" } }}>
      <Box
        component="summary"
        sx={{ typography: "s3", fontWeight: "fontWeightBold" }}
      >
        {title}
      </Box>
      <Box sx={{ pt: 0.75, pl: 1.5 }}>{children}</Box>
    </Box>
  );
}
Bucket.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function agentLine(nudge, summary) {
  const label = named(nudge.triggerAgentVersion);
  if (summary.agentNotMoved) {
    const text =
      COPY.agentNotMoved[summary.agentNotMoved] || COPY.agentNotMoved.other;
    return text(label);
  }
  return summary.agentDefaultMoved ? COPY.agentMoved(label) : null;
}

// What a landed, unneeded or failed rebuild did, names only: the three
// buckets, what was set aside, reused, left out or not checked, its warnings,
// whether the agent became the default, and the problems that stopped it.
function NudgeOutcome({ nudge }) {
  const summary = nudge.summary || {};
  const piece = summary.piece || nudge.target;
  const lines = [];
  if (summary.setAside) {
    lines.push(COPY.setAside(summary.setAside.startedFromLabel));
  }
  if (nudge.state === "applied" && summary.line) lines.push(summary.line);
  if (summary.reused?.label) lines.push(COPY.reused(summary.reused.label));
  if (nudge.target === "environment" && !NUDGE_ACTIVE_STATES.has(nudge.state)) {
    const agent = agentLine(nudge, summary);
    if (agent) lines.push(agent);
  }
  if (summary.notChecked?.length) {
    lines.push(COPY.notChecked(summary.notChecked));
  }
  const draftEdits = summary.draftEdits;
  const problems = nudge.error?.problems || [];
  return (
    <Stack spacing={0.75}>
      {lines.map((line) => (
        <Line key={line}>{line}</Line>
      ))}
      {(summary.warnings || []).map((warning) => (
        <Line key={warning} color="warning.main">
          {warning}
        </Line>
      ))}
      {summary.leftOut?.length > 0 && (
        <Box component="ul" sx={{ m: 0, pl: 2 }} aria-label={COPY.leftOut}>
          {summary.leftOut.map((one) => (
            <Box component="li" key={one.key}>
              <Line>
                {COPY.leftOut}: {one.key}
                {one.why ? ` — ${one.why}` : ""}
              </Line>
            </Box>
          ))}
        </Box>
      )}
      {nudge.state === "applied" && (
        <>
          {draftEdits && !isEmptyDiff(piece, draftEdits) && (
            <Bucket title={COPY.buckets.draftEdits(draftEdits.edited_by)}>
              <VersionDiff piece={piece} diff={draftEdits} />
            </Bucket>
          )}
          {summary.update && !isEmptyDiff(piece, summary.update) && (
            <Bucket title={COPY.buckets.update}>
              <VersionDiff piece={piece} diff={summary.update} />
            </Bucket>
          )}
          {summary.both?.length > 0 && (
            <Bucket title={COPY.buckets.both}>
              <Line mono color="text.primary">
                {summary.both.map((one) => one.item).join(", ")}
              </Line>
            </Bucket>
          )}
        </>
      )}
      {problems.length > 0 && (
        <Box component="ul" sx={{ m: 0, pl: 2 }}>
          {problems.map((problem) => (
            <Box
              component="li"
              key={`${problem.item}:${problem.kind}:${problem.message}`}
            >
              <Line color="text.primary">
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {problem.item}
                </Box>
                {problem.message ? `: ${problem.message}` : ""}
                {problem.inYourUnsavedEdits && (
                  <Box
                    component="span"
                    sx={{ ml: 0.75, color: "warning.main", fontWeight: 600 }}
                  >
                    ({COPY.inYourEdits})
                  </Box>
                )}
              </Line>
            </Box>
          ))}
        </Box>
      )}
      {nudge.state === "failed" &&
        nudge.error?.code &&
        nudge.error.code !== "environment_deleted" && (
          <Line>
            {summary.setAside
              ? COPY.nothingChangedSetAside
              : COPY.nothingChanged}
          </Line>
        )}
    </Stack>
  );
}
NudgeOutcome.propTypes = { nudge: PropTypes.object.isRequired };

// What became of a nudge. It takes focus after a Skip from here, so focus
// stays on the card and the new state is read out.
export function NudgeStateLine({ focus = false, onFocused, sx, children }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!focus) return;
    ref.current?.focus();
    onFocused?.();
  }, [focus, onFocused]);
  return (
    <Typography
      ref={ref}
      role="status"
      tabIndex={-1}
      sx={{ typography: "s2", color: "text.secondary", ...sx }}
    >
      {children}
    </Typography>
  );
}
NudgeStateLine.propTypes = {
  focus: PropTypes.bool,
  onFocused: PropTypes.func,
  sx: PropTypes.object,
  children: PropTypes.node,
};

// A rebuild's state line and what it did.
export function NudgeStatus({ nudge, focus, onFocused, sx }) {
  return (
    <>
      <NudgeStateLine focus={focus} onFocused={onFocused} sx={sx}>
        {stateText(nudge)}
      </NudgeStateLine>
      <NudgeOutcome nudge={nudge} />
    </>
  );
}
NudgeStatus.propTypes = {
  nudge: PropTypes.object.isRequired,
  focus: PropTypes.bool,
  onFocused: PropTypes.func,
  sx: PropTypes.object,
};

// A nudge in the builder chat: its own system turn. Without the nudge itself
// (versioning off, or the list not loaded yet) the message's text stands in.
export default function NudgeCard({ turn }) {
  const { nudge } = turn;
  if (!nudge) {
    return (
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {turn.text}
      </Typography>
    );
  }
  const title = heading(nudge);
  const pending = nudge.state === "pending";
  const actions = (
    <NudgeActions
      nudge={nudge}
      subject={title}
      nudgesEnabled={turn.nudgesEnabled}
      answering={turn.answering}
      rebuilding={turn.rebuilding}
      stopping={turn.stopping}
      error={turn.error}
      onRebuild={turn.onRebuild}
      onSkipNudge={turn.onSkipNudge}
      onStopNudge={turn.onStopNudge}
    />
  );
  return (
    <Box
      data-testid={`nudge-card-${nudge.id}`}
      component="section"
      aria-label={title}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 1.5,
      }}
    >
      <Stack spacing={1}>
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
            {title}
          </Typography>
          {pending &&
            nudge.target === "environment" &&
            nudge.facts.agentDefaultMoved === false && (
              <Line>{COPY.notDefault}</Line>
            )}
        </Box>
        <NudgeFacts nudge={nudge} />
        {pending ? (
          <>
            <Typography sx={{ typography: "s2" }}>
              {COPY.question[nudge.target] || COPY.question.environment}
            </Typography>
            <DraftNotice draft={turn.draft} />
            {actions}
          </>
        ) : (
          <>
            <NudgeStatus
              nudge={nudge}
              focus={turn.skipped}
              onFocused={turn.onSkippedFocused}
            />
            {actions}
          </>
        )}
      </Stack>
    </Box>
  );
}
NudgeCard.propTypes = {
  turn: PropTypes.shape({
    id: PropTypes.string,
    text: PropTypes.string,
    nudge: PropTypes.object,
    nudgesEnabled: PropTypes.bool,
    answering: PropTypes.bool,
    rebuilding: PropTypes.bool,
    stopping: PropTypes.bool,
    skipped: PropTypes.bool,
    onSkippedFocused: PropTypes.func,
    error: PropTypes.object,
    // The piece's open draft, from the drafts list.
    draft: PropTypes.object,
    onRebuild: PropTypes.func,
    onSkipNudge: PropTypes.func,
    onStopNudge: PropTypes.func,
  }).isRequired,
};
