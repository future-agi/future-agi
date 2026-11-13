import { Fragment, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  CircularProgress,
  Collapse,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import CustomTooltip from "src/components/tooltip";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import {
  useReadiness,
  useReadinessCases,
  useRecheck,
  useRereadWhenDefaultsMove,
  useRereadWhenDone,
} from "src/api/simulate-environments/readiness";
import {
  FIX_ACTIVE_STATES,
  useFixRun,
  useFollowFixRun,
  useStartGroupFix,
} from "src/api/simulate-environments/readinessFix";
import {
  awaitedCheck,
  useDrafts,
  useEnvironmentVersions,
  useRetryAgentVersion,
} from "src/api/simulate-environments/versions";
import CredentialsForm from "../../agents/CredentialsForm";
import { PreflightBadge, PreflightReport } from "../../agents/PreflightStatus";
import {
  activationText,
  checkMissing,
  named,
} from "../../agents/agentVersionStatus";
import { AGENT_VERSIONS_COPY } from "../../workspace.constants";
import { readinessView } from "./readinessView";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";
import * as fixDummy from "src/api/simulate-environments/readinessFix.dummy"; // dummy:

const lineSx = {
  borderTop: "1px solid",
  borderColor: "divider",
  px: 1.5,
  py: 1,
};

const boxSx = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

// Until the report lands nothing is known to be blocked or fine, so Run waits.
// A report that fails to load leaves Run as it was: unknown is not blocked.
const LOADING_VIEW = { canRun: false, loading: true, summary: null, open: [] };

const STATE_ROLE = {
  checking: "running",
  failed: "failed",
  ours: "unavailable",
  retry: "untested",
  unchecked: "untested",
  unavailable: "unavailable",
  refused: "failed",
};

function stateText(state, reason) {
  const text = COPY.states[state];
  return typeof text === "function" ? text(reason) : text;
}

const spinner = <CircularProgress size={12} color="inherit" />;

// A fix run's state for a line; a line nobody started keeps its Fix button.
const PROGRESS_ROLE = {
  queued: "neutral",
  fixing: "info",
  waiting_on_you: "warning",
  waiting_on_other: "neutral",
  fixed: "success",
  could_not_fix: "danger",
};

// Lines the chat can still take on: not started, or it gave up.
const fixable = (line) =>
  !line.progress ||
  ["not_started", "could_not_fix"].includes(line.progress.state);

const GROUP_ICON = {
  needs_you: "solar:user-linear",
  builder: "solar:chat-round-dots-linear",
  recheck: "solar:refresh-linear",
};

const dividerTop = { borderTop: "1px solid", borderColor: "divider" };

// One group: its own bordered card, a tinted header and its lines inside.
function GroupCard({ group, count, lead, action, note, children }) {
  return (
    <Box
      component="section"
      aria-labelledby={`readiness-group-${group}`}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Box sx={{ bgcolor: "background.neutral", px: 1.5, py: 0.75 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Box sx={{ width: 18, flexShrink: 0, display: "flex" }}>
            {lead ?? (
              <Iconify
                icon={GROUP_ICON[group]}
                width={16}
                sx={{ color: "text.secondary" }}
              />
            )}
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              id={`readiness-group-${group}`}
              component="h3"
              sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
            >
              {COPY.groups[group]}
              <Box
                component="span"
                sx={{
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                }}
              >
                {` · ${count}`}
              </Box>
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {COPY.groupHints[group]}
            </Typography>
          </Box>
          {action}
        </Stack>
        {note}
      </Box>
      {children}
    </Box>
  );
}

GroupCard.propTypes = {
  group: PropTypes.oneOf(["needs_you", "builder", "recheck"]).isRequired,
  count: PropTypes.number.isRequired,
  // Stands in for the group's icon, e.g. a select-all checkbox.
  lead: PropTypes.node,
  // On the right of the header, e.g. the send button.
  action: PropTypes.node,
  // Under the header, e.g. why sending is held.
  note: PropTypes.node,
  children: PropTypes.node,
};

const MAX_NAMES = 10;
// The cases route reads at most 200 scenario keys.
const MAX_KEYS = 200;

// A header count that names its scenarios on hover: the first page the
// server lists, and how many more from the count itself.
function CountTip({ envId, picks, keys, bucket, count, report, children }) {
  const [shown, setShown] = useState(false);
  const tooMany = (keys?.length || 0) > MAX_KEYS;
  const cases = useReadinessCases(envId, {
    picks,
    bucket,
    keys,
    report,
    enabled: shown && !tooMany,
  });
  const names = (cases.data || [])
    .slice(0, MAX_NAMES)
    .map((one) => ({ key: one.key, name: one.name || one.key }));
  let title = COPY.namesLoading;
  if (tooMany) title = COPY.namesTooMany;
  else if (cases.isError) title = COPY.namesError;
  else if (cases.data) {
    title = (
      <Box component="ul" sx={{ m: 0, pl: 2 }}>
        {names.map((one) => (
          <Box component="li" key={one.key}>
            {one.name}
          </Box>
        ))}
        {count > names.length && (
          <Box component="li" sx={{ listStyle: "none", ml: -2 }}>
            {COPY.namesMore(count - names.length)}
          </Box>
        )}
      </Box>
    );
  }
  return (
    <CustomTooltip
      show
      title={title}
      size="small"
      arrow
      onOpen={() => setShown(true)}
    >
      <Box
        component="span"
        tabIndex={0}
        sx={{ textDecoration: "underline dotted", cursor: "default" }}
      >
        {children}
      </Box>
    </CustomTooltip>
  );
}

CountTip.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  bucket: PropTypes.string.isRequired,
  count: PropTypes.number,
  report: PropTypes.string,
  children: PropTypes.node,
};

// The version new credential values were saved as, and how its check went.
function SavedCredentials({ version }) {
  return (
    <Box data-testid="credentials-saved">
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {AGENT_VERSIONS_COPY.credentials.saved(named(version))}
        </Typography>
        <PreflightBadge status={version.preflightStatus} />
      </Stack>
      {version.preflightStatus === "failed" && (
        <PreflightReport report={version.preflightReport} />
      )}
      {activationText(version) && (
        <Typography
          sx={{ typography: "s3", color: "status.warning.fg", mt: 0.5 }}
        >
          {activationText(version)}
        </Typography>
      )}
    </Box>
  );
}

SavedCredentials.propTypes = { version: PropTypes.object.isRequired };

function FixLine({
  line,
  onRecheck,
  onRetryAgent,
  busy,
  inFlight,
  open,
  onToggle,
  checking,
  envId,
  credentials,
  saved,
  onSaved,
  progress,
  waitingOnLabel,
  grouped,
  select,
  onFixOne,
  fixOneLabel,
}) {
  const { fix } = line;
  // The builder chat makes this line's change; its re-check follows the fix.
  const inChat = Boolean(grouped) && line.group === "builder";
  const label = COPY.areas[line.area] || line.area;
  const state = PROGRESS_ROLE[progress?.state] ? progress.state : null;
  const progressText = COPY.progress[state];
  // While the chat works on a line, its badge stands in for Fix.
  const offersFix = !state || state === "could_not_fix";
  const showReason =
    progress?.reason?.message &&
    (state === "could_not_fix" || state === "waiting_on_you");
  // Checking the pair again is this line's whole fix: it opens on the full
  // reason and what it means, not an instruction that only says "check again".
  const recheckOnly = fix?.type === "recheck" && fix.recheck?.scope === "pair";
  return (
    // While a check runs these are the last results: dimmed, no Fix. A fixed
    // line is the result of a check that passed, so it stays as is.
    <Box
      sx={{
        ...lineSx,
        opacity: checking && !inFlight && state !== "fixed" ? 0.5 : 1,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center">
        {inChat && (
          // Lines already being fixed keep the space, so names line up.
          <Box sx={{ width: 18, flexShrink: 0, display: "flex" }}>
            {select && (
              <Checkbox
                size="small"
                checked={select.checked}
                onChange={(event) => select.onChange(event.target.checked)}
                inputProps={{ "aria-label": COPY.selectLine(label) }}
                sx={{ p: 0 }}
              />
            )}
          </Box>
        )}
        <Box sx={{ flexShrink: 0 }}>
          <StatusBadge status={line.status} label={label} variant="dot" />
        </Box>
        {/* One line here; the full reason and what it means on hover. */}
        <CustomTooltip
          show={Boolean(line.reason)}
          title={
            <Box sx={{ whiteSpace: "pre-line" }}>
              {[line.reason, line.consequence].filter(Boolean).join("\n\n")}
            </Box>
          }
          size="small"
          arrow
        >
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {line.reason}
            {line.affected ? ` · ${COPY.affected(line.affected)}` : ""}
          </Typography>
        </CustomTooltip>
        {state && (
          <StatusBadge
            status={state}
            role={PROGRESS_ROLE[state]}
            label={
              typeof progressText === "function"
                ? progressText(waitingOnLabel)
                : progressText
            }
            size="sm"
            icon={state === "fixing" ? spinner : undefined}
          />
        )}
        {fix && !checking && offersFix && (
          <IconButton
            size="small"
            onClick={onToggle}
            aria-label={COPY.details(label)}
            aria-expanded={open}
            aria-controls={`readiness-fix-${line.area}`}
            sx={{ flexShrink: 0, p: 0.25 }}
          >
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={16}
              sx={{
                transition: "transform 0.2s",
                transform: open ? "rotate(180deg)" : "none",
              }}
            />
          </IconButton>
        )}
      </Stack>
      {showReason && (
        <Typography sx={{ typography: "s3", color: "text.secondary", pt: 0.5 }}>
          {progress.reason.message}
        </Typography>
      )}
      {fix && (
        <Collapse
          id={`readiness-fix-${line.area}`}
          in={open && offersFix && (!checking || inFlight)}
          unmountOnExit
        >
          <Stack spacing={1} sx={{ pt: 1, pl: 2.5 }}>
            {/* A re-check line opens on its full reason and what it means;
                a fix line on what to change. */}
            <Typography sx={{ typography: "s3", color: "text.primary" }}>
              {recheckOnly ? line.reason : fix.action}
            </Typography>
            {recheckOnly && line.consequence && (
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {line.consequence}
              </Typography>
            )}
            {!recheckOnly && fix.would && (
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {COPY.after(fix.would)}
              </Typography>
            )}
            {saved && <SavedCredentials version={saved} />}
            {credentials && !inFlight && (
              <CredentialsForm
                envId={envId}
                version={credentials}
                onSaved={onSaved}
                onCancel={onToggle}
                extras={false}
              />
            )}
            {inChat && fix.message && (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  px: 1.25,
                  py: 1,
                }}
              >
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {COPY.sendsToChat}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.primary" }}>
                  {fix.message}
                </Typography>
              </Box>
            )}
            {inChat && onFixOne && (
              <Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={busy}
                  onClick={onFixOne}
                >
                  {fixOneLabel}
                </Button>
              </Box>
            )}
            {!inChat && fix.recheck && (!credentials || inFlight) && (
              <Box>
                <Button
                  size="small"
                  variant="contained"
                  disabled={busy}
                  startIcon={inFlight ? spinner : null}
                  onClick={
                    fix.recheck.scope === "agent" ? onRetryAgent : onRecheck
                  }
                >
                  {inFlight && COPY.checking}
                  {!inFlight &&
                    (fix.recheck.scope === "agent"
                      ? COPY.retryAgent
                      : COPY.recheckPair)}
                </Button>
              </Box>
            )}
          </Stack>
        </Collapse>
      )}
    </Box>
  );
}

FixLine.propTypes = {
  line: PropTypes.object.isRequired,
  onRecheck: PropTypes.func.isRequired,
  onRetryAgent: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  checking: PropTypes.bool,
  // This line's own re-check is the one running.
  inFlight: PropTypes.bool,
  open: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  envId: PropTypes.string,
  // The agent version whose missing values this line's fix takes, if any.
  credentials: PropTypes.object,
  // The version those values were saved as.
  saved: PropTypes.object,
  onSaved: PropTypes.func,
  // The fix run's state for this line, from the server.
  progress: PropTypes.object,
  // The line a waiting_on_other line waits on.
  waitingOnLabel: PropTypes.string,
  // Shown in groups; the builder chat's lines get a checkbox column.
  grouped: PropTypes.bool,
  // Sends only this line to the builder chat.
  onFixOne: PropTypes.func,
  fixOneLabel: PropTypes.string,
  // Its checkbox, while the chat can take it on: { checked, onChange }.
  select: PropTypes.shape({
    checked: PropTypes.bool.isRequired,
    onChange: PropTypes.func.isRequired,
  }),
};

/**
 * Whether the picked agent, environment and scenario versions can run
 * together: what is blocked and why, what to fix, and a Re-check of the pair.
 * The popup reads the counts and whether Run is allowed through `onView`.
 */
export default function ReadyToRunSection({
  envId,
  picks,
  keys = null,
  trials = 1,
  onView,
  onGoToChat,
}) {
  const readiness = useReadiness(envId, picks, { keys, trials });
  const recheck = useRecheck(envId);
  const retryAgent = useRetryAgentVersion(envId);
  // Which line's button started the check, so that button shows it running.
  const [pressed, setPressed] = useState(null);
  // One fix open at a time keeps the section short; the popup body scrolls.
  const [openArea, setOpenArea] = useState(null);
  // After an agent retry, until the report has been read again.
  const [refreshing, setRefreshing] = useState(false);
  const view = useMemo(
    () => (readiness.data ? readinessView(readiness.data) : null),
    [readiness.data],
  );
  const versions = useEnvironmentVersions(envId);
  // The agent version new credential values were saved as.
  const [savedId, setSavedId] = useState(null);
  const agentVersions = [...(versions.data?.agent || [])];
  fixDummy.addMissingKeys(agentVersions); // dummy:
  const agentId = readiness.data?.pairing?.agent_version?.id;
  const agentVersion = agentVersions.find((one) => one.id === agentId);
  // New values are saved over the default version only, so the form is offered
  // for the default's own missing values.
  const credentials =
    agentVersion?.isDefault && checkMissing(agentVersion).length
      ? agentVersion
      : null;
  const saved = savedId
    ? agentVersions.find((one) => one.id === savedId) || null
    : null;
  const savedChecking = Boolean(saved && awaitedCheck(saved));
  // Once the saved version's check lands it may be the default.
  useRereadWhenDone(envId, savedChecking);

  const grouped = Boolean(view?.grouped);
  const fixRun = useFixRun(envId, { enabled: grouped }).data ?? null;
  useFollowFixRun(envId, fixRun);
  const start = useStartGroupFix(envId);
  // A refusal belongs to the run state it met; once the run moves on, drop it.
  const fixState = fixRun?.state;
  const resetStart = start.reset;
  useEffect(() => {
    resetStart();
  }, [fixState, resetStart]);
  const { data: drafts } = useDrafts(envId, { enabled: grouped });
  useRereadWhenDefaultsMove(envId, versions.data?.defaults);
  // While a fix runs, the user follows it and answers in the builder chat.
  const chatShown = FIX_ACTIVE_STATES.has(fixRun?.state);
  // Ticks the user changed, by row id. Before a fix, every line starts ticked;
  // while one runs, lines start unticked and are added on purpose.
  const [ticks, setTicks] = useState(() => new Map());

  // From the click until the server says it is checking, Run waits too.
  const sending =
    recheck.isPending || retryAgent.isPending || refreshing || savedChecking;
  // Another pairing starts with nothing open and nothing running.
  const picksKey = JSON.stringify(picks || {});
  useEffect(() => {
    setPressed(null);
    setOpenArea(null);
    setSavedId(null);
    setTicks(new Map());
  }, [picksKey]);

  // Gone from the popup: it no longer holds Run or shows held-back counts.
  useEffect(() => () => onView?.(null), [onView]);

  useEffect(() => {
    if (readiness.isLoading) onView?.(LOADING_VIEW);
    else onView?.(view && sending ? { ...view, canRun: false } : view);
  }, [view, onView, readiness.isLoading, sending]);

  if (readiness.isLoading) {
    return (
      <Box sx={boxSx}>
        <ContentSkeleton variant="panel" rows={3} label={COPY.title} />
      </Box>
    );
  }
  if (!view) {
    return readiness.isError ? (
      <ErrorState title={COPY.loadError} onRetry={() => readiness.refetch()} />
    ) : null;
  }

  // A re-check reads as running from the click, not from the next report.
  const checking = view.state === "checking" || sending;
  const dimSx = { ...lineSx, opacity: checking ? 0.5 : 1 };
  const busy = checking;
  const runRecheck = (from = null) => {
    setPressed(from);
    recheck.mutate(picks);
  };
  const runRetryAgent = (from = null) => {
    setPressed(from);
    setRefreshing(true);
    retryAgent.mutate(
      { versionId: agentId },
      {
        onSettled: async () => {
          try {
            await readiness.refetch();
          } finally {
            setRefreshing(false);
          }
        },
      },
    );
  };
  const blockers = view.open.filter((line) => line.status === "blocked").length;
  const readyLabels = view.ready.map((area) => COPY.areas[area] || area);
  // Grading is ready only once some scenario is known to be gradable.
  if (view.grading.gradable > 0 && !view.grading.notGradable) {
    readyLabels.push(COPY.areas.grading);
  }
  // A re-check or retry the server turned down says so, in its words.
  const refusal = recheck.isError ? recheck.error : retryAgent.error;
  const cantCheck = recheck.data?.validation?.state === "unavailable";
  let noteState = view.state;
  if (checking) noteState = "checking";
  else if (refusal) noteState = "refused";
  else if (cantCheck) noteState = "unavailable";
  const note = stateText(
    noteState,
    refusal ? refusal.message || refusal.detail : view.stateReason,
  );

  const renderLine = (line) => (
    <FixLine
      key={line.area}
      line={line}
      onRecheck={() => runRecheck(line.area)}
      onRetryAgent={() => runRetryAgent(line.area)}
      inFlight={checking && pressed === line.area}
      open={openArea === line.area}
      onToggle={() =>
        setOpenArea((area) => (area === line.area ? null : line.area))
      }
      checking={checking}
      busy={busy}
      envId={envId}
      credentials={line.fix?.type === "preflight" ? credentials : null}
      saved={line.fix?.type === "preflight" ? saved : null}
      onSaved={(data) => {
        setPressed(line.area);
        setSavedId(data?.agent_version?.id || null);
      }}
      progress={line.progress}
      waitingOnLabel={COPY.areas[line.waitingOnArea]}
      grouped={view.grouped}
      onFixOne={
        line.group === "builder" && fixable(line) && !unsaved
          ? () => sendRows([line.rowId])
          : undefined
      }
      fixOneLabel={chatShown ? COPY.addOne : COPY.fixOne}
      select={
        view.grouped && line.group === "builder" && fixable(line)
          ? {
              checked: ticked(line),
              onChange: (on) =>
                setTicks((all) => new Map(all).set(line.rowId, on)),
            }
          : null
      }
    />
  );
  const builder = view.groups.builder;
  const ticked = (line) => ticks.get(line.rowId) ?? !chatShown;
  const takeable = builder.filter(fixable);
  const chosen = takeable.filter(ticked);
  const sendRows = (rows) =>
    start.mutate(
      { group: "builder", rows, picks },
      { onSuccess: () => setTicks(new Map()) },
    );
  const sendChosen = () => sendRows(chosen.map((line) => line.rowId));
  // A group's select-all box, for the lines it can still take on.
  const selectAll = (lines, name) => {
    const open = lines.filter(fixable);
    if (!open.length) return null;
    const on = open.filter(ticked).length;
    return (
      <Checkbox
        size="small"
        checked={on === open.length}
        indeterminate={on > 0 && on < open.length}
        onChange={(event) =>
          setTicks((all) => {
            const next = new Map(all);
            open.forEach((line) => next.set(line.rowId, event.target.checked));
            return next;
          })
        }
        inputProps={{ "aria-label": name }}
        sx={{ p: 0 }}
      />
    );
  };
  const unsaved = Boolean(drafts?.any);
  const fixRefusal =
    start.isError &&
    (COPY.fixRefused[start.error?.error] ??
      start.error?.message ??
      COPY.fixRefused.default);

  return (
    <Box data-testid="ready-to-run" sx={boxSx}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
          {COPY.title}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {COPY.summary(view.summary, blockers).map((part, at) => (
            <Fragment key={part.bucket || "blockers"}>
              {at > 0 && " · "}
              {part.bucket && part.count > 0 ? (
                <CountTip
                  envId={envId}
                  picks={picks}
                  keys={keys}
                  bucket={part.bucket}
                  count={part.count}
                  report={readiness.data?.computed_at}
                >
                  {part.text}
                </CountTip>
              ) : (
                part.text
              )}
            </Fragment>
          ))}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={busy}
          startIcon={checking ? spinner : null}
          onClick={() => runRecheck()}
          sx={{ py: 0, flexShrink: 0 }}
        >
          {checking ? COPY.checking : COPY.recheck}
        </Button>
      </Stack>
      {note && (
        // Dot and text spaced as a badge's own dot and label, so this text
        // starts where the area names below start.
        <Stack direction="row" spacing={0.75} alignItems="center" sx={lineSx}>
          <StatusBadge
            status={STATE_ROLE[noteState]}
            label=""
            variant="dot"
            sx={{ gap: 0 }}
          />
          <Typography
            aria-live="polite"
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            {note}
          </Typography>
        </Stack>
      )}
      {!view.grouped && view.open.map(renderLine)}
      {view.grouped && (
        <Stack spacing={1} sx={{ px: 1.5, py: 1.25, ...dividerTop }}>
          {view.groups.needs_you.length > 0 && (
            <GroupCard group="needs_you" count={view.groups.needs_you.length}>
              {view.groups.needs_you.map(renderLine)}
            </GroupCard>
          )}
          {builder.length > 0 && (
            <GroupCard
              group="builder"
              count={builder.length}
              lead={selectAll(builder, COPY.selectAll)}
              action={
                takeable.length > 0 ? (
                  <Button
                    size="small"
                    variant="contained"
                    disabled={
                      !chosen.length || checking || start.isPending || unsaved
                    }
                    aria-describedby={unsaved ? "fix-chat-unsaved" : undefined}
                    onClick={sendChosen}
                    sx={{ py: 0.25, flexShrink: 0 }}
                  >
                    {chatShown
                      ? COPY.addSelected(chosen.length)
                      : COPY.sendSelected(chosen.length)}
                  </Button>
                ) : null
              }
              note={
                <>
                  {takeable.length > 0 && unsaved && (
                    <Typography
                      id="fix-chat-unsaved"
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {COPY.fixesUnsaved}
                    </Typography>
                  )}
                  {fixRefusal && (
                    <Typography
                      role="alert"
                      sx={{ typography: "s3", color: "status.danger.fg" }}
                    >
                      {fixRefusal}
                    </Typography>
                  )}
                </>
              }
            >
              {builder.map(renderLine)}
              {chatShown && (
                <Stack
                  data-testid="fix-chat-note"
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{ ...lineSx, bgcolor: "background.neutral" }}
                >
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
                  >
                    {COPY.chatFixing(
                      builder
                        .filter((line) => fixRun?.rows?.includes(line.rowId))
                        .map((line) => COPY.areas[line.area] || line.area),
                    )}
                    {!onGoToChat && ` · ${COPY.chatFollow}`}
                  </Typography>
                  {onGoToChat && (
                    <Button size="small" color="inherit" onClick={onGoToChat}>
                      {COPY.goToChat}
                    </Button>
                  )}
                </Stack>
              )}
            </GroupCard>
          )}
          {view.groups.recheck.length > 0 && (
            <GroupCard
              group="recheck"
              count={view.groups.recheck.length}
              action={
                <Button
                  size="small"
                  variant="outlined"
                  disabled={busy}
                  startIcon={checking ? spinner : null}
                  onClick={() => runRecheck()}
                  sx={{ py: 0.25, flexShrink: 0 }}
                >
                  {checking ? COPY.checking : COPY.recheck}
                </Button>
              }
            >
              {view.groups.recheck.map(renderLine)}
            </GroupCard>
          )}
        </Stack>
      )}
      {view.limits.map((line) => (
        <Stack
          key={line.area}
          direction="row"
          spacing={1}
          alignItems="center"
          sx={dimSx}
        >
          <StatusBadge
            status={line.status}
            label={COPY.areas[line.area] || line.area}
            variant="dot"
          />
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
          >
            {line.reason}
          </Typography>
        </Stack>
      ))}
      {view.grading.notGradable > 0 && (
        <Stack direction="row" spacing={1} alignItems="center" sx={dimSx}>
          <StatusBadge
            status="partial"
            label={COPY.areas.grading}
            variant="dot"
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {COPY.notGradable(view.grading.notGradable)}
          </Typography>
        </Stack>
      )}
      {readyLabels.length > 0 && (
        <Stack direction="row" spacing={0.75} alignItems="center" sx={dimSx}>
          <StatusBadge
            status="supported"
            label=""
            variant="dot"
            sx={{ gap: 0 }}
          />
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
          >
            {COPY.moreReady(
              readyLabels,
              view.open.length === 0 && view.limits.length === 0,
            )}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

ReadyToRunSection.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  trials: PropTypes.number,
  onView: PropTypes.func,
  // Set where the page has no builder chat to follow the fix in.
  onGoToChat: PropTypes.func,
};
