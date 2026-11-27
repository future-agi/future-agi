import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fToNow } from "src/utils/format-time";
import { useRunAsk, useRunAsks } from "src/api/simulate-environments/runAsks";

import {
  ASK_STATUS,
  COUNTED_STATES,
  humanize,
  plural,
  stateLabel,
} from "./asks.constants";

/**
 * The questions asked about this run's calls, newest first. Each shows, per
 * question, how many calls were requested and how each one ended; opening it
 * lists every call with its answers, each call linking to the call itself.
 */
export default function RunAsksPanel({
  executionId,
  openAskId = null,
  onOpenAsk,
  onOpenCall,
  onAsk = null,
  askDisabledReason = null,
}) {
  const {
    data,
    isLoading,
    error,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useRunAsks(executionId);
  const asks = data?.pages?.flatMap((page) => page.results) ?? [];
  const total = data?.pages?.[0]?.count ?? 0;

  return (
    <Box data-testid="run-asks">
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Typography sx={{ typography: "s1", fontWeight: 600, flex: 1 }}>
          Questions about this run
        </Typography>
        {onAsk && (
          // A disabled button takes no hover, so the reason sits on a span.
          <CustomTooltip
            show={!!askDisabledReason}
            size="small"
            arrow
            title={askDisabledReason}
          >
            <span data-testid="ask-every-call">
              <Button
                variant="outlined"
                size="small"
                onClick={onAsk}
                disabled={!!askDisabledReason}
                startIcon={
                  <Iconify icon="solar:question-circle-linear" width={15} />
                }
              >
                Ask about every call
              </Button>
            </span>
          </CustomTooltip>
        )}
      </Stack>
      {isLoading && <CircularProgress size={20} />}
      {error && (
        <Alert severity="error">
          {error?.message || "Couldn't read this run's questions"}
        </Alert>
      )}
      {!isLoading && !error && asks.length === 0 && (
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          No questions asked yet. Tick calls in Test runs and choose Ask a
          question, or ask about every call.
        </Typography>
      )}
      <Stack spacing={1.5}>
        {asks.map((ask) => (
          <AskCard
            key={ask.id}
            ask={ask}
            executionId={executionId}
            open={ask.id === openAskId}
            onToggle={() => onOpenAsk(ask.id === openAskId ? null : ask.id)}
            onOpenCall={onOpenCall}
          />
        ))}
      </Stack>
      {hasNextPage && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5 }}>
          <Button
            size="small"
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage ? "Loading…" : "Show older questions"}
          </Button>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {`${asks.length.toLocaleString()} of ${plural(total, "question")} shown`}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
RunAsksPanel.propTypes = {
  executionId: PropTypes.string,
  openAskId: PropTypes.string,
  onOpenAsk: PropTypes.func.isRequired,
  onOpenCall: PropTypes.func.isRequired,
  onAsk: PropTypes.func,
  askDisabledReason: PropTypes.string,
};

function AskCard({ ask, executionId, open, onToggle, onOpenCall }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
      }}
      data-testid={`ask-${ask.id}`}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1 }}>
          {[
            ask.selection_kind === "calls" ? "Selected calls" : "Every call",
            plural(ask.requested, "call"),
            ask.asked_by && `asked by ${ask.asked_by}`,
            ask.created_at && fToNow(ask.created_at),
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        <Chip
          size="small"
          label={ASK_STATUS[ask.status] ?? humanize(ask.status)}
          color={ask.status === "stopped" ? "warning" : "default"}
        />
        <Button size="small" onClick={onToggle} aria-expanded={open}>
          {open ? "Hide calls" : "Show calls"}
        </Button>
      </Stack>
      {ask.status === "stopped" && ask.stop_cause && (
        <Typography sx={{ typography: "s3", color: "warning.main", mt: 0.5 }}>
          {`Stopped: ${humanize(ask.stop_cause)}`}
        </Typography>
      )}
      <Stack spacing={1} sx={{ mt: 1 }}>
        {ask.questions.map((question) => (
          <QuestionCounts key={question.key} question={question} />
        ))}
      </Stack>
      {open && (
        <AskCalls
          executionId={executionId}
          askId={ask.id}
          onOpenCall={onOpenCall}
        />
      )}
    </Box>
  );
}
AskCard.propTypes = {
  ask: PropTypes.object.isRequired,
  executionId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  onOpenCall: PropTypes.func.isRequired,
};

/**
 * Requested, the six counts and pending; the answers as "x of N answered",
 * where N is only the calls that were answered. Errors and unmeasured calls are
 * never in it.
 */
export function QuestionCounts({ question }) {
  const { counts } = question;
  const answered = counts.rate.answered;
  const labels = Object.entries(counts.rate.labels);
  return (
    <Box data-testid={`ask-question-${question.key}`}>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {question.name}
      </Typography>
      <Typography
        sx={{ typography: "s3", fontVariantNumeric: "tabular-nums" }}
        data-testid="ask-counts"
      >
        {[
          `${plural(counts.requested, "call")} requested`,
          ...COUNTED_STATES.map(({ key, label }) => `${label} ${counts[key]}`),
          counts.pending > 0 && `Pending ${counts.pending}`,
          counts.calls_gone > 0 &&
            `${plural(counts.calls_gone, "call")} deleted since`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </Typography>
      {answered > 0 && (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle" }}
          data-testid="ask-rate"
        >
          {labels
            .map(([label, n]) => `${label}: ${n} of ${answered} answered`)
            .join(" · ")}
        </Typography>
      )}
    </Box>
  );
}
QuestionCounts.propTypes = { question: PropTypes.object.isRequired };

/** One line per call, one cell per question; the call's name opens it. */
function AskCalls({ executionId, askId, onOpenCall }) {
  const { data, isLoading, error } = useRunAsk(executionId, askId);
  const [callsShown, setCallsShown] = useState(50);
  useEffect(() => setCallsShown(50), [askId]);

  if (isLoading) return <CircularProgress size={20} sx={{ mt: 1 }} />;
  if (error)
    return (
      <Alert severity="error" sx={{ mt: 1 }}>
        {error?.message || "Couldn't read this ask"}
      </Alert>
    );
  const questions = data?.questions ?? [];
  const calls = callsOf(data?.results ?? []);
  return (
    <Box sx={{ mt: 1.5, overflowX: "auto" }}>
      <Table size="small" aria-label="Answers per call">
        <TableHead>
          <TableRow>
            <TableCell>Call</TableCell>
            {questions.map((question, index) => (
              <TableCell
                key={question.key}
              >{`Question ${index + 1}`}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {calls.slice(0, callsShown).map((call) => (
            <TableRow key={call.id}>
              <TableCell>
                <Link
                  component="button"
                  type="button"
                  onClick={() => onOpenCall(call.id)}
                  underline="hover"
                  sx={{ textAlign: "left" }}
                >
                  {call.name}
                </Link>
              </TableCell>
              {questions.map((question) => (
                <AnswerCell
                  key={question.key}
                  row={call.answers[question.key]}
                />
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {calls.length > callsShown && (
        <Button size="small" onClick={() => setCallsShown((n) => n + 50)}>
          {`Show more (${calls.length - callsShown} left)`}
        </Button>
      )}
    </Box>
  );
}
AskCalls.propTypes = {
  executionId: PropTypes.string,
  askId: PropTypes.string.isRequired,
  onOpenCall: PropTypes.func.isRequired,
};

function AnswerCell({ row }) {
  if (!row) return <TableCell>—</TableCell>;
  const answered = row.state === "answered";
  return (
    <TableCell title={row.reason || (row.cause ? humanize(row.cause) : "")}>
      <Typography
        sx={{
          typography: "s2",
          color: answered ? "text.primary" : "text.subtitle",
        }}
      >
        {answered ? row.answer : stateLabel(row.state)}
      </Typography>
      {!answered && row.cause && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {humanize(row.cause)}
        </Typography>
      )}
    </TableCell>
  );
}
AnswerCell.propTypes = { row: PropTypes.object };

// The backend's rows come call by call, question by question, in the order
// the calls were picked.
function callsOf(rows) {
  const byCall = new Map();
  rows.forEach((row) => {
    const id = row.call_execution_id;
    if (!byCall.has(id))
      byCall.set(id, { id, name: row.call_name, answers: {} });
    byCall.get(id).answers[row.question_key] = row;
  });
  return [...byCall.values()];
}
