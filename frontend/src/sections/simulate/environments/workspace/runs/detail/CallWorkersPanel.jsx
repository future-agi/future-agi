import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { CircularProgress, Stack, Typography } from "@mui/material";
import { useCallWorkers } from "src/api/simulate-environments/runDetail";
import { fDateTime } from "src/utils/format-time";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
import { PlacedChecks, WorkerChecks } from "./CallWorkersChecks";
import ParticipantsHeader from "./participants/ParticipantsHeader";
import SequenceDiagram from "./participants/SequenceDiagram";
import { participantsFromRecord } from "./participants/participantsFromRecord";
import { sequenceOf } from "./participants/sequenceOf";

export default function CallWorkersPanel({
  callExecutionId,
  callStatus = null,
  callerName = null,
  onOpenTrace,
  onShowInTranscript,
  attempt = null,
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
    <Stack spacing={1.25} sx={{ p: 1.5 }}>
      <ParticipantsHeader
        summary={handOffs ? C.handOffSummary(data.counts) : null}
        seq={seq}
        onPick={setSelected}
      />
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
      ) : data.spans_received === false ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.sandboxOnly}
        </Typography>
      ) : null}
      {data.workers.length ? (
        <Typography
          data-testid="supported-boundary"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {C.supportedBoundary}
          {data.beyond_boundary ? (
            <span data-testid="beyond-boundary"> {C.beyondBoundary}</span>
          ) : null}
        </Typography>
      ) : null}
      {seq?.steps.length ? (
        <SequenceDiagram
          seq={seq}
          selected={selected}
          onSelect={setSelected}
          // Open in trace waits for the record to name its trace project.
          onOpenTrace={data.project_id ? onOpenTrace : undefined}
          onShowInTranscript={onShowInTranscript}
        />
      ) : null}
      {data.workers.some((one) => one.checks.length) ? (
        <WorkerChecks workers={data.workers} />
      ) : null}
      {data.placements.length ? (
        <PlacedChecks placements={data.placements} names={names} />
      ) : null}
      {notObserved ? (
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
  // Opens one of the call's traces in its Traces tab, at the step's span when it has one.
  onOpenTrace: PropTypes.func,
  // Opens the transcript at a step: ({ turn, at }) with `at` in seconds from the record's start.
  onShowInTranscript: PropTypes.func,
  // An earlier attempt's id: its record, as the rerun kept it.
  attempt: PropTypes.string,
};
