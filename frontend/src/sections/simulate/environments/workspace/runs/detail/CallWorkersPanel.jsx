import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { CircularProgress, Stack, Typography } from "@mui/material";
import { keyframes } from "@mui/material/styles";
import { useCallWorkers } from "src/api/simulate-environments/runDetail";
import { fDateTime } from "src/utils/format-time";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
import { PlacedChecks, WorkerChecks } from "./CallWorkersChecks";
import ParticipantsHeader from "./participants/ParticipantsHeader";
import SequenceDiagram from "./participants/SequenceDiagram";
import {
  participantsFromRecord,
  recordStart,
  recordingSeconds,
} from "./participants/participantsFromRecord";
import { sequenceOf } from "./participants/sequenceOf";
import TrialRoutes from "./participants/TrialRoutes";
import { STEP_PARAM, linkedStepKey } from "./participants/stepLink";

const easeIn = keyframes`
  from { opacity: 0; }
  to { opacity: 1; }
`;

export default function CallWorkersPanel({
  callExecutionId,
  callStatus = null,
  callerName = null,
  callStartedAt = null,
  onOpenTrace,
  onShowInTranscript,
  attempt = null,
  testExecutionId = null,
  scenarioKey = null,
  onOpenTrial,
}) {
  const { data, isError } = useCallWorkers(
    callExecutionId,
    callStatus,
    attempt,
  );
  const [selected, setSelected] = useState(null);
  const seq = useMemo(
    () =>
      data?.state === "recorded"
        ? sequenceOf(participantsFromRecord(data), { callerName })
        : null,
    [data, callerName],
  );
  // A link to a step names the API's own step key. It opens that step once per call, so a refetch keeps the user's pick.
  const linkedFor = useRef(null);
  useEffect(() => {
    // A step key belongs to one record: a link names the latest attempt's, never an earlier one's.
    if (!seq || attempt || linkedFor.current === callExecutionId) return;
    linkedFor.current = callExecutionId;
    const key = linkedStepKey();
    const step = key && seq.steps.find((one) => String(one.key) === key);
    if (step) setSelected(step.n);
  }, [seq, callExecutionId, attempt]);
  // A failed refetch keeps the tree it has. A call known to have no record never gets here: the drawers drop the tab.
  if (!data)
    return isError ? (
      <Typography sx={{ p: 2, typography: "s1", color: "error.main" }}>
        {C.error}
      </Typography>
    ) : (
      <CircularProgress size={20} sx={{ m: 2 }} />
    );
  // The server says why there is no record: the read failed, or an earlier attempt was kept before records were.
  if (data.state !== "recorded")
    return (
      <Typography sx={{ p: 2, typography: "s1", color: "text.secondary" }}>
        {C.states[data.state]}
      </Typography>
    );

  const workers = Object.fromEntries(
    data.workers.map((one) => [one.worker_id, one]),
  );
  // Not seen: the server sends no coordinator, and the timeline says Unknown.
  const coordinator = data.coordinator ? data.coordinator.name : C.unknown;
  const names = {
    coordinator,
    ofStep: (step) => workers[step.worker_id]?.name ?? C.unknown,
    ofWorker: (id) => (id ? workers[id]?.name ?? C.unknown : coordinator),
    workerOf: (id) => workers[id],
  };
  const handOffs = data.counts.hand_offs;
  const linkOf = (step) => {
    const url = new URL(window.location.href);
    url.searchParams.set(STEP_PARAM, String(step.key));
    return url.toString();
  };
  const showInTranscript = onShowInTranscript
    ? ({ turn, at }) =>
        onShowInTranscript({
          turn,
          at: recordingSeconds(at, recordStart(data), callStartedAt),
        })
    : undefined;
  const named = (test) =>
    data.workers
      .filter(test)
      .map((one) => one.name)
      .join(", ");
  const notObserved = named((one) => !one.observed);
  const noEnd = named((one) => one.state === "observed_no_end");
  const pastDeadline = named((one) => one.past_deadline);
  const bypassed = named((one) => one.channel_bypassed);
  const cutShort = data.workers
    .filter((one) => one.cut_short)
    .map((one) => `${one.name} (${C.cutShort[one.state]})`)
    .join(", ");
  const notDeclared = named((one) => one.undeclared);
  // A cut-short worker's cleanup, as the API computed it when served; an old record has none.
  const cleanups = data.workers
    .filter((one) => one.cleanup && C.cleanup[one.cleanup.status])
    .map((one) => {
      const copy = C.cleanup[one.cleanup.status];
      return {
        id: one.worker_id,
        text: C.workerCleanup(
          one.name,
          typeof copy === "function"
            ? copy(one.cleanup.at ? fDateTime(one.cleanup.at) : "")
            : copy,
        ),
      };
    });

  return (
    <Stack
      spacing={1.25}
      sx={{
        p: 1.5,
        // Each call's record eases in, so a switch between trials reads as a change; the trials table holds still.
        "& > :not([data-trial-routes])": {
          animation: `${easeIn} 200ms ease-out`,
          "@media (prefers-reduced-motion: reduce)": { animation: "none" },
        },
      }}
    >
      <ParticipantsHeader
        summary={handOffs ? C.handOffSummary(data.counts) : null}
        notes={[
          data.spans_received === false &&
            data.collection !== "no_evidence" &&
            data.collection !== "partial" &&
            data.collection !== "unavailable" &&
            C.sandboxOnly,
          data.workers.length && C.supportedBoundary,
          data.workers.length && data.beyond_boundary && C.beyondBoundary,
        ].filter(Boolean)}
        seq={seq}
        onPick={setSelected}
      />
      {/* The scenario's trials as they ran last: an earlier attempt has no place among them. */}
      {!attempt && testExecutionId && scenarioKey ? (
        <TrialRoutes
          testExecutionId={testExecutionId}
          scenarioKey={scenarioKey}
          currentId={callExecutionId}
          onOpen={(id, stepKey) => {
            if (id !== callExecutionId) return onOpenTrial?.(id, stepKey);
            const step = seq?.steps.find((one) => one.key === stepKey);
            if (step) setSelected(step.n);
          }}
        />
      ) : null}
      {data.stopped_by_user &&
      data.collection !== "no_evidence" &&
      data.events.length > 0 ? (
        <Typography
          data-testid="stopped-by-user"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {C.stoppedByUser}
        </Typography>
      ) : null}
      {data.collection === "no_evidence" ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {data.stopped_by_user ? C.stoppedByUserNoEvidence : C.noEvidence}
        </Typography>
      ) : data.collection === "partial" || data.collection === "unavailable" ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {data.collection === "partial"
            ? C.partialCollection
            : C.unavailableCollection}
        </Typography>
      ) : null}
      {seq?.steps.length ? (
        <SequenceDiagram
          seq={seq}
          selected={selected}
          onSelect={setSelected}
          // Open in trace waits for the record's trace project. The transcript, Traces and step links hold the
          // latest attempt only, so an earlier attempt offers none of them.
          onOpenTrace={!attempt && data.project_id ? onOpenTrace : undefined}
          onShowInTranscript={attempt ? undefined : showInTranscript}
          linkOf={attempt ? undefined : linkOf}
        />
      ) : null}
      {data.workers.some((one) => one.checks.length) ? (
        <WorkerChecks workers={data.workers} />
      ) : null}
      {data.placements.length ? (
        <PlacedChecks placements={data.placements} names={names} />
      ) : null}
      {/* The diagram names them itself when it is drawn. */}
      {notObserved && !seq?.steps.length ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersNotObserved(notObserved)}
        </Typography>
      ) : null}
      {noEnd ? (
        <Typography
          data-testid="workers-no-end"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {C.workersNoEnd(noEnd)}
        </Typography>
      ) : null}
      {cutShort ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersCutShort(cutShort)}
        </Typography>
      ) : null}
      {bypassed ? (
        <Typography
          data-testid="workers-bypassed"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {C.workersBypassed(bypassed)}
        </Typography>
      ) : null}
      {pastDeadline ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersPastDeadline(pastDeadline)}
        </Typography>
      ) : null}
      {cleanups.map((one) => (
        <Typography
          key={one.id}
          data-testid="worker-cleanup"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {one.text}
        </Typography>
      ))}
      {notDeclared ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersNotDeclared(notDeclared)}
        </Typography>
      ) : null}
      {data.events_omitted ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.eventsOmitted(data.events_omitted)}
        </Typography>
      ) : null}
    </Stack>
  );
}

CallWorkersPanel.propTypes = {
  callExecutionId: PropTypes.string,
  // The call's status, so a call that ends while open is read again.
  callStatus: PropTypes.string,
  // The caller's name for the first lane.
  callerName: PropTypes.string,
  // When the call itself started, so a step's time lands on the recording's clock.
  callStartedAt: PropTypes.string,
  // Opens one of the call's traces in its Traces tab, at the step's span when it has one.
  onOpenTrace: PropTypes.func,
  // Opens the transcript at a step: ({ turn, at }) with `at` in seconds from the record's start.
  onShowInTranscript: PropTypes.func,
  // An earlier attempt's id: its record, as the rerun kept it.
  attempt: PropTypes.string,
  // The run and the call's scenario, to line up the scenario's trials.
  testExecutionId: PropTypes.string,
  scenarioKey: PropTypes.string,
  // (callExecutionId, stepKey | null) → opens another trial of the scenario, on that step.
  onOpenTrial: PropTypes.func,
};
