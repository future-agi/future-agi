import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  SUBJECT_SHAPE,
  bulkBody,
  bulkFiltersProblem,
  useBulkRedeliver,
  useBulkRedeliverCount,
  useWebhookDeliveries,
} from "src/api/webhooks";
import { useDebounce } from "src/hooks/use-debounce";
import {
  DELIVERY_STATUS,
  EVENT_LABELS,
  OUTCOME_TEXT,
  fmtDateTime,
  fmtMs,
} from "./copy";
import { apiError } from "./form";
import DeliveryDrawer from "./DeliveryDrawer";

// The log reads the prompt or deployment filter once typing pauses, and only in
// its full shape (WH-3 review S10): a half-typed "prompt:" is never sent.
export const SUBJECT_DEBOUNCE_MS = 400;

const TIME_WINDOWS = [
  { value: "", label: "Any time" },
  { value: "1", label: "Last hour" },
  { value: "24", label: "Last 24 hours" },
  { value: "168", label: "Last 7 days" },
];

function answerOf(last) {
  if (!last) return "–";
  if (last.response_status) return String(last.response_status);
  return OUTCOME_TEXT[last.outcome] || last.outcome;
}

// Deliveries (DESIGN.md section 11.2): columns When · Event · # · Status ·
// Attempts · Answer · Latency; filters; bulk "Redeliver failed (N)" over the
// filtered view (section 8.3) with the dry-run count; a row opens the 720 px
// drawer.
export default function DeliveriesCard({ endpoint, access }) {
  const [status, setStatus] = useState("");
  const [eventType, setEventType] = useState("");
  const [hours, setHours] = useState("");
  const [subject, setSubject] = useState("");
  const [seqFrom, setSeqFrom] = useState("");
  const [seqTo, setSeqTo] = useState("");
  const [open, setOpen] = useState(null);
  const [confirm, setConfirm] = useState(null);
  // After a bulk redelivery the moved deliveries may sit outside the filtered
  // view, so the log keeps refreshing for a minute whatever it shows.
  const [pollUntil, setPollUntil] = useState(0);
  const typedSubject = subject.trim();
  const settledSubject = useDebounce(typedSubject, SUBJECT_DEBOUNCE_MS);
  const settledWellFormed =
    !settledSubject || SUBJECT_SHAPE.test(settledSubject);
  // The subject the log (and so the bulk) applies: the last settled,
  // well-formed value; a malformed one keeps the previous view.
  const [logSubject, setLogSubject] = useState("");
  useEffect(() => {
    if (settledWellFormed) setLogSubject(settledSubject);
  }, [settledSubject, settledWellFormed]);

  // The time window is kept as the hours chosen; the request turns it into a
  // start time when it is made. A start time computed here would change the
  // query key on every render and refetch without end.
  const filters = {
    status,
    event_type: eventType,
    subject: logSubject,
    hours,
    seq_from: seqFrom,
    seq_to: seqTo,
  };
  const {
    data,
    dataUpdatedAt,
    isLoading,
    isError,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useWebhookDeliveries(endpoint.id, filters, {
    refetchInterval: (q) => {
      const rows = (q.state.data?.pages || []).flatMap(
        (p) => p?.data?.result?.results || [],
      );
      const inFlight = rows.some((r) =>
        ["pending", "sending", "retrying"].includes(r.status),
      );
      return inFlight || Date.now() < pollUntil ? 2500 : false;
    },
  });
  const rows = data?.rows || [];
  const bulk = useBulkRedeliver(endpoint.id);
  const canBulk = access.can_admin || (endpoint.org_wide && access.org_admin);
  // The bulk acts on the filtered view: the event type, the prompt or
  // deployment, the time window and the # range go to the bulk API as they go
  // to the log. The status filter does not narrow it (it always redelivers
  // failed ones). While a prompt or deployment is still being typed, or a # is
  // not a whole number, the bulk waits rather than act on another set.
  const bulkFilters = {
    event_type: eventType,
    hours,
    subject: logSubject,
    seq_from: seqFrom,
    seq_to: seqTo,
  };
  // The bulk acts on what the log shows: while the typed subject is not yet
  // the log's (being typed, or malformed), it waits.
  const bulkProblem =
    bulkFiltersProblem({ ...bulkFilters, subject: typedSubject }) ||
    (typedSubject !== logSubject
      ? "The log is still applying the filter."
      : null);
  const bulkBlocked = !!bulkProblem;
  // N follows the log: it is counted again every time the log is read, and
  // only then (the query never fetches by itself).
  const failedCount = useBulkRedeliverCount(endpoint.id, bulkFilters, {
    enabled: false,
  });
  const { refetch: refetchCount } = failedCount;
  useEffect(() => {
    if (canBulk && !bulkBlocked && dataUpdatedAt) refetchCount();
  }, [dataUpdatedAt, canBulk, bulkBlocked, refetchCount]);
  const n = bulkBlocked ? 0 : failedCount.data ?? 0;

  // One start time for the dry run and the real run, so both count the same set.
  const askBulk = () => {
    const body = bulkBody(endpoint.id, bulkFilters);
    bulk.mutate(
      { ...body, dry_run: true },
      { onSuccess: (res) => setConfirm({ ...res.data.result, body }) },
    );
  };
  const runBulk = () => {
    bulk.mutate(
      { ...confirm.body, dry_run: false },
      {
        onSuccess: () => {
          setConfirm(null);
          setPollUntil(Date.now() + 60_000);
        },
      },
    );
  };

  return (
    <Card
      variant="outlined"
      sx={{ p: 2.5 }}
      component="section"
      aria-label="Deliveries"
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 1.5 }}
      >
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
          Deliveries
        </Typography>
        {canBulk && (
          <Tooltip title={bulkProblem || ""}>
            <span>
              <Button
                size="small"
                variant="outlined"
                onClick={askBulk}
                disabled={bulkBlocked || !n || bulk.isPending}
              >
                {`Redeliver failed (${n.toLocaleString()})`}
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mb: 1.5 }}>
        <TextField
          select
          size="small"
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="">Any status</MenuItem>
          {Object.entries(DELIVERY_STATUS).map(([v, s]) => (
            <MenuItem key={v} value={v}>
              {s.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Event"
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="">Any event</MenuItem>
          {Object.keys(EVENT_LABELS).map((t) => (
            <MenuItem key={t} value={t}>
              {t}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Prompt or deployment"
          placeholder="prompt:<id> or deployment:<id>"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          error={!settledWellFormed}
          helperText={
            settledWellFormed ? undefined : "Use prompt:<id> or deployment:<id>"
          }
          sx={{ minWidth: 220 }}
        />
        <TextField
          select
          size="small"
          label="Time"
          value={hours}
          onChange={(e) => setHours(e.target.value)}
          sx={{ minWidth: 140 }}
        >
          {TIME_WINDOWS.map((w) => (
            <MenuItem key={w.value} value={w.value}>
              {w.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          type="number"
          label="# from"
          value={seqFrom}
          onChange={(e) => setSeqFrom(e.target.value)}
          sx={{ width: 100 }}
          inputProps={{ min: 1 }}
        />
        <TextField
          size="small"
          type="number"
          label="# to"
          value={seqTo}
          onChange={(e) => setSeqTo(e.target.value)}
          sx={{ width: 100 }}
          inputProps={{ min: 1 }}
        />
      </Stack>

      {isError && (
        <Alert severity="error">The delivery log could not be loaded.</Alert>
      )}
      {!isLoading && !isError && rows.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {Object.values(filters).some(Boolean)
            ? "No deliveries match these filters."
            : "No deliveries yet. Send a test to see one here."}
        </Typography>
      )}
      {rows.length > 0 && (
        <Table size="small" aria-label="Delivery log">
          <TableHead>
            <TableRow>
              <TableCell>When</TableCell>
              <TableCell>Event</TableCell>
              <TableCell>#</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Attempts</TableCell>
              <TableCell>Answer</TableCell>
              <TableCell>Latency</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((d) => {
              const st = DELIVERY_STATUS[d.status] || {
                label: d.status,
                tone: "default",
              };
              return (
                <TableRow
                  key={d.id}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() => setOpen(d.id)}
                >
                  <TableCell>
                    {fmtDateTime(d.last_attempt?.started_at || d.created_at)}
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>
                    {d.event_type}
                    {d.is_test && (
                      <Chip size="small" label="Test" sx={{ ml: 1 }} />
                    )}
                  </TableCell>
                  <TableCell>{d.seq ?? "–"}</TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      variant="soft"
                      color={st.tone}
                      label={st.label}
                    />
                  </TableCell>
                  <TableCell>{d.attempts_count}</TableCell>
                  <TableCell>{answerOf(d.last_attempt)}</TableCell>
                  <TableCell>{fmtMs(d.last_attempt?.duration_ms)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {hasNextPage && (
        <Box sx={{ mt: 1 }}>
          <Button
            size="small"
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            Load more
          </Button>
        </Box>
      )}
      {bulk.isError && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {apiError(bulk.error, "Could not redeliver.").message}
        </Alert>
      )}
      {bulk.isSuccess && !bulk.data.data.result.dry_run && (
        <Alert severity="success" sx={{ mt: 1 }} role="status">
          {bulk.data.data.result.queued} deliveries queued again.
        </Alert>
      )}

      <Dialog open={!!confirm} onClose={() => setConfirm(null)}>
        <DialogTitle>Redeliver failed ({confirm?.count ?? 0})</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            {confirm?.count
              ? `${confirm.count} failed ${confirm.count === 1 ? "delivery" : "deliveries"} will be sent again, with the same ids.`
              : "No failed deliveries to send again."}
            {confirm?.expired
              ? ` ${confirm.expired} are too old: their bodies are no longer kept.`
              : ""}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!confirm?.count || bulk.isPending}
            onClick={runBulk}
          >
            Redeliver {confirm?.count ?? 0}
          </Button>
        </DialogActions>
      </Dialog>

      <DeliveryDrawer
        deliveryId={open}
        endpoint={endpoint}
        onClose={() => setOpen(null)}
      />
    </Card>
  );
}

DeliveriesCard.propTypes = {
  endpoint: PropTypes.object.isRequired,
  access: PropTypes.object.isRequired,
};
