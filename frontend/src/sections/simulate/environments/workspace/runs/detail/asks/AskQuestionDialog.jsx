import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import {
  MAX_ASK_QUESTIONS,
  MAX_ASK_QUESTION_LENGTH,
  askBody,
  checkAsk,
  labelsOf,
  launchAsk,
} from "src/api/simulate-environments/runAsks";

import {
  ASK_KINDS,
  causeLabel,
  costLabel,
  kindLabel,
  plural,
} from "./asks.constants";

// Each question keeps its own id, so removing one never hands its typing to
// the next.
let lastQuestionId = 0;
const blankQuestion = () => ({
  id: `question-${(lastQuestionId += 1)}`,
  question: "",
  answerType: "yes_no",
  labels: "",
  kinds: ["transcript"],
});

// Refusals that mean what the person saw is out of date: the dialog checks
// again and shows the new numbers before anything can launch.
const STALE = new Set(["selection_changed", "cost_changed"]);

const questionReady = (one) =>
  one.question.trim() &&
  one.kinds.length > 0 &&
  (one.answerType !== "category" || labelsOf(one).length >= 2);

/**
 * Ask up to five questions about calls in this run. Check shows what the ask
 * would read and cost, and writes nothing; Ask launches what was checked. Any
 * change after a check throws it away, so what launches is what was seen.
 *
 * `selection` is `{ kind: "runs" }` (every call in the run) or
 * `{ kind: "calls", callIds }` (the ticked calls).
 */
export default function AskQuestionDialog({
  open,
  onClose,
  executionId,
  selection,
  onLaunched,
}) {
  const [questions, setQuestions] = useState(() => [blankQuestion()]);
  const [check, setCheck] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null); // "checking" | null
  // Only the latest check may land: an edit while a check is out drops it.
  const requestRef = useRef(0);
  // A launch is never dropped. Closing the dialog or editing while it starts
  // does not let a second one start, and its ask still opens once it lands.
  const [launching, setLaunching] = useState(false);
  const launchingRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    requestRef.current += 1;
    setQuestions([blankQuestion()]);
    setCheck(null);
    setError(null);
    setBusy(null);
  }, [open, selection]);

  const edit = (index, patch) => {
    requestRef.current += 1;
    setBusy(null);
    setCheck(null);
    setError(null);
    setQuestions((current) =>
      current.map((one, at) => (at === index ? { ...one, ...patch } : one)),
    );
  };
  const addQuestion = () => {
    requestRef.current += 1;
    setBusy(null);
    setCheck(null);
    setError(null);
    setQuestions((current) => [...current, blankQuestion()]);
  };
  const removeQuestion = (index) => {
    requestRef.current += 1;
    setBusy(null);
    setCheck(null);
    setError(null);
    setQuestions((current) => current.filter((_, at) => at !== index));
  };

  const body = selection ? askBody(selection, questions) : null;
  const ready = !!body && questions.every(questionReady);

  // After a stale launch the refusal stays on screen above the new numbers.
  const runCheck = async ({ keepError = false } = {}) => {
    requestRef.current += 1;
    const request = requestRef.current;
    setBusy("checking");
    if (!keepError) setError(null);
    setCheck(null);
    try {
      const result = await checkAsk(executionId, body);
      // One key per check: the same launch sent again (a retry after a lost
      // response) returns the ask it started instead of starting another.
      if (request === requestRef.current)
        setCheck({ ...result, launchKey: uuidv4() });
    } catch (failure) {
      if (request === requestRef.current)
        setError(failure?.message || "Couldn't check these calls");
    } finally {
      if (request === requestRef.current) setBusy(null);
    }
  };

  const launch = async () => {
    if (launchingRef.current) return;
    launchingRef.current = true;
    setLaunching(true);
    setError(null);
    // Edited since? Then a stale refusal does not check the new questions.
    const request = requestRef.current;
    try {
      const launched = await launchAsk(executionId, body, check);
      onLaunched?.(launched.id);
    } catch (failure) {
      setError(failure?.message || "Couldn't start the ask");
      if (request === requestRef.current && STALE.has(failure?.code))
        runCheck({ keepError: true });
    } finally {
      launchingRef.current = false;
      setLaunching(false);
    }
  };

  const wholeRun = selection?.kind !== "calls";
  const canLaunch =
    !!check && !check.refusal && check.answers > 0 && !busy && !launching;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Ask a question</DialogTitle>
      <DialogContent dividers>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 2 }}>
          {wholeRun
            ? "About every call in this run."
            : `About the ${plural(selection?.callIds?.length ?? 0, "selected call")}.`}{" "}
          Each call is read and answered on its own. Nothing is charged until
          you ask.
        </Typography>

        <Stack spacing={2}>
          {questions.map((one, index) => (
            <QuestionForm
              key={one.id}
              index={index}
              value={one}
              removable={questions.length > 1}
              onChange={(patch) => edit(index, patch)}
              onRemove={() => removeQuestion(index)}
            />
          ))}
          {questions.length < MAX_ASK_QUESTIONS && (
            <Box>
              <Button
                size="small"
                onClick={addQuestion}
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={15} />
                }
              >
                Add another question
              </Button>
            </Box>
          )}
        </Stack>

        {launching && !check && (
          <Alert severity="info" sx={{ mt: 2 }} data-testid="ask-starting">
            Your last question is still starting. It opens on the Questions tab
            once it has.
          </Alert>
        )}
        {error && (
          <Alert severity="error" sx={{ mt: 2 }} data-testid="ask-error">
            {error}
          </Alert>
        )}
        {check && (
          <CheckSummary
            check={check}
            questionIds={questions.map((one) => one.id)}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="outlined"
          onClick={() => runCheck()}
          disabled={!ready || !!busy || launching}
        >
          {busy === "checking" ? "Checking…" : "Check"}
        </Button>
        <Button variant="contained" onClick={launch} disabled={!canLaunch}>
          {launching
            ? "Starting…"
            : check?.answers
              ? `Ask (${plural(check.answers, "answer")})`
              : "Ask"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
AskQuestionDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  executionId: PropTypes.string,
  selection: PropTypes.shape({
    kind: PropTypes.oneOf(["runs", "calls"]).isRequired,
    callIds: PropTypes.arrayOf(PropTypes.string),
  }),
  onLaunched: PropTypes.func,
};

function QuestionForm({ index, value, removable, onChange, onRemove }) {
  const toggleKind = (kind, checked) =>
    onChange({
      kinds: checked
        ? [...value.kinds, kind]
        : value.kinds.filter((one) => one !== kind),
    });
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
      }}
      data-testid={`ask-question-${index + 1}`}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          {`Question ${index + 1}`}
        </Typography>
        {removable && (
          <IconButton
            size="small"
            onClick={onRemove}
            aria-label={`Remove question ${index + 1}`}
          >
            <Iconify icon="solar:trash-bin-minimalistic-linear" width={16} />
          </IconButton>
        )}
      </Stack>
      <TextField
        fullWidth
        multiline
        minRows={2}
        size="small"
        label="What do you want to know about each call?"
        value={value.question}
        onChange={(event) => onChange({ question: event.target.value })}
        inputProps={{ maxLength: MAX_ASK_QUESTION_LENGTH }}
      />
      <RadioGroup
        row
        value={value.answerType}
        onChange={(event) => onChange({ answerType: event.target.value })}
        sx={{ mt: 1 }}
      >
        <FormControlLabel
          value="yes_no"
          control={<Radio size="small" />}
          label="Yes or no"
        />
        <FormControlLabel
          value="category"
          control={<Radio size="small" />}
          label="One of my answers"
        />
      </RadioGroup>
      {value.answerType === "category" && (
        <TextField
          fullWidth
          multiline
          minRows={2}
          size="small"
          label="Answers, one per line (2 to 10)"
          value={value.labels}
          onChange={(event) => onChange({ labels: event.target.value })}
          sx={{ mt: 1 }}
        />
      )}
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1.5 }}>
        Read from each call
      </Typography>
      <FormGroup row>
        {ASK_KINDS.map((kind) => (
          <FormControlLabel
            key={kind.value}
            control={
              <Checkbox
                size="small"
                checked={value.kinds.includes(kind.value)}
                onChange={(event) =>
                  toggleKind(kind.value, event.target.checked)
                }
              />
            }
            label={kind.label}
          />
        ))}
      </FormGroup>
    </Box>
  );
}
QuestionForm.propTypes = {
  index: PropTypes.number.isRequired,
  value: PropTypes.shape({
    question: PropTypes.string,
    answerType: PropTypes.string,
    labels: PropTypes.string,
    kinds: PropTypes.arrayOf(PropTypes.string),
  }).isRequired,
  removable: PropTypes.bool.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

/** What the check found: per question, ready, unmeasured and why. */
function CheckSummary({ check, questionIds }) {
  return (
    <Box sx={{ mt: 2 }} data-testid="ask-check">
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {`${plural(check.requested, "call")} requested`}
      </Typography>
      <Stack spacing={1} sx={{ mt: 1 }}>
        {/* The check returns the questions in the order they were sent, and any
            edit throws it away, so the form's questions line up with it. */}
        {check.questions.map((one, index) => (
          <Box key={questionIds[index] ?? one.name}>
            <Typography sx={{ typography: "s2" }}>
              {`Question ${index + 1}: ${one.ready} ready · ${one.unmeasured} unmeasured · ${one.not_applicable} not applicable`}
            </Typography>
            {missingParts(one).map((line) => (
              <Typography
                key={line}
                sx={{ typography: "s3", color: "text.subtitle", pl: 2 }}
              >
                {line}
              </Typography>
            ))}
          </Box>
        ))}
      </Stack>
      <Typography sx={{ typography: "s2", mt: 1.5 }}>
        {`${plural(check.answers, "answer")} · ${costLabel(check)}`}
      </Typography>
      {check.refusal && (
        <Alert severity="warning" sx={{ mt: 1.5 }} data-testid="ask-refusal">
          {check.refusal.message}
        </Alert>
      )}
    </Box>
  );
}
CheckSummary.propTypes = {
  check: PropTypes.object.isRequired,
  questionIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

// "Transcript: 2 never uploaded" — why calls are unmeasured, per kind.
function missingParts(question) {
  return Object.entries(question.by_kind || {}).flatMap(([kind, row]) => {
    const missing = Object.entries(row?.not_captured || {}).filter(
      ([, count]) => count > 0,
    );
    if (!missing.length) return [];
    return [
      `${kindLabel(kind)}: ${missing
        .map(([cause, count]) => `${count} ${causeLabel(cause)}`)
        .join(", ")}`,
    ];
  });
}
