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
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
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
  const notDeclared = named((one) => one.undeclared);

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
      {data.collection === "no_evidence" ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.noEvidence}
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
      {notObserved ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersNotObserved(notObserved)}
        </Typography>
      ) : null}
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
