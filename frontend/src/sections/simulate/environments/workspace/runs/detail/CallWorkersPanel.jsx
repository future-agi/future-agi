import PropTypes from "prop-types";
import { useState } from "react";
import {
  CircularProgress,
  FormControlLabel,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { useCallWorkers } from "src/api/simulate-environments/runDetail";
import { fDateTime } from "src/utils/format-time";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
import { PlacedChecks, WorkerChecks } from "./CallWorkersChecks";
import { EventList } from "./CallWorkersEventList";
import { Turn } from "./CallWorkersParts";
import { WorkersTable } from "./CallWorkersTable";
import { callEvents, stepsByKey, turnLabel } from "./utils/callWorkersUtils";

// A call with more tool calls than this opens with their JSON closed, so a long record stays quick to draw.
const MAX_OPEN_CALLS = 20;
const NO_TRACES = [];

export default function CallWorkersPanel({
  callExecutionId,
  callStatus = null,
  onOpenTrace,
  traceIds = NO_TRACES,
}) {
  const { data, isError } = useCallWorkers(callExecutionId, callStatus);
  const [rawEvents, setRawEvents] = useState(false);
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
  const coordinator = data.coordinator?.name || C.coordinator;
  const names = {
    coordinator,
    ofStep: (step) => workers[step.worker_id]?.name || C.unknown,
    ofWorker: (id) => (id ? workers[id]?.name || C.unknown : coordinator),
    workerOf: (id) => workers[id],
  };
  const start = data.started_at;
  const byKey = stepsByKey(data);
  // Each failed or unsure check the API places on a step, shown on that step's row.
  const placedOn = {};
  data.placements.forEach((one) => {
    if (one.step !== null) (placedOn[one.step] ||= []).push(one);
  });
  const handOffs = data.counts.hand_offs;
  // With no hand-off there is nothing to tabulate, so the steps are the whole story.
  const showSteps = rawEvents || !handOffs;
  const events = showSteps ? callEvents(data, byKey, names) : [];
  const openAll =
    data.steps.filter((one) => one.kind === "tool_call").length <=
    MAX_OPEN_CALLS;
  const named = (test) =>
    data.workers
      .filter(test)
      .map((one) => one.name)
      .join(", ");
  const notObserved = named((one) => !one.observed);
  const pastDeadline = named((one) => one.past_deadline);
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
      {handOffs ? (
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Typography sx={{ typography: "s1", fontWeight: 600, flex: 1 }}>
            {C.handOffSummary(data.counts)}
          </Typography>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={rawEvents}
                onChange={(e) => setRawEvents(e.target.checked)}
              />
            }
            label={C.rawEvents}
            slotProps={{ typography: { typography: "s1" } }}
            sx={{ m: 0, flexShrink: 0, gap: 0.5 }}
          />
        </Stack>
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
      {showSteps ? (
        events.length ? (
          <EventList
            bordered
            events={events}
            start={start}
            openJson={() => openAll}
            onOpenTrace={onOpenTrace}
            traceIds={traceIds}
          />
        ) : null
      ) : (
        data.turns.map((round) => {
          const label = turnLabel(round, data.turns);
          const roundRows = round.handoffs.map((key) => byKey[key]);
          return (
            <Stack key={round.turn} spacing={1.25} data-testid="turn">
              {!round.expects_messages && !round.request ? null : (
                <Turn
                  title={`${label}${C.request}`}
                  turn={round.request}
                  start={start}
                  empty={C.noRequest}
                />
              )}
              {roundRows.length ? (
                <WorkersTable
                  rows={roundRows}
                  byKey={byKey}
                  names={names}
                  placedOn={placedOn}
                  start={start}
                  onOpenTrace={onOpenTrace}
                  traceIds={traceIds}
                />
              ) : null}
              {round.calls_outside ? (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {C.callsOutsideTable(round.calls_outside)}
                </Typography>
              ) : null}
              {!round.expects_messages && !round.response ? null : (
                <Turn
                  title={`${label}${C.response}`}
                  turn={round.response}
                  agent
                  start={start}
                  empty={C.noReply}
                />
              )}
            </Stack>
          );
        })
      )}
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
      {cutShort ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersCutShort(cutShort)}
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
  // Opens one of the call's traces in its Traces tab, at the step's span when it has one.
  onOpenTrace: PropTypes.func,
  // The traces that tab lists: a step offers to open only one of these.
  traceIds: PropTypes.arrayOf(PropTypes.string),
};
