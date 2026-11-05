import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  IconButton,
  MenuItem,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import {
  ANSWER_TYPES,
  EXAMPLES,
  MAX_CATEGORIES,
  MAX_QUESTIONS,
  READS,
  aboutCredits,
  estimateOf,
} from "../../_mock/trialQuestions";
import { FIELDS_OF_READS } from "../../_mock/evalEvidence";
import ScopePicker, { ScopeReview } from "./ScopePicker";
import { useEvalScope } from "./useEvalScope";

/*
  Ask about these trials — Observe's Instant eval composer, pointed at one
  run's saved trials. Nothing runs until Evaluate: the count, what can't be
  read and the credits are all said first.
*/
const blank = () => ({ text: "", type: ANSWER_TYPES.YES_NO, labelsText: "" });
const labelsFrom = (s) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .slice(0, MAX_CATEGORIES);

const FieldLabel = ({ children }) => (
  <Typography
    sx={{ typography: "s1", fontWeight: 500, color: "text.primary", mb: 0.75 }}
  >
    {children}
  </Typography>
);
FieldLabel.propTypes = { children: PropTypes.node };

export default function AskTrialsComposer({
  ctx,
  filter,
  selectedIds,
  runLabel,
  initial,
  onCancel,
  onAsk,
}) {
  const [questions, setQuestions] = useState(() =>
    initial?.questions?.length
      ? initial.questions.map((q) => ({
          text: q.text,
          type: q.type,
          labelsText:
            q.type === ANSWER_TYPES.CATEGORY ? q.labels.join(", ") : "",
        }))
      : [blank()],
  );
  const [reads, setReads] = useState(initial?.reads || "conversation");
  const fields = useMemo(
    () => FIELDS_OF_READS[reads] || ["transcript"],
    [reads],
  );
  const state = useEvalScope({ ctx, filter, selectedIds, fields, initial });
  const { scope, trials, readable, scopeLabel } = state;
  const ready = questions.filter((q) => q.text.trim());
  const invalid = questions.some(
    (q) =>
      q.text.trim() &&
      q.type === ANSWER_TYPES.CATEGORY &&
      labelsFrom(q.labelsText).length < 2,
  );

  const setQ = (i, next) =>
    setQuestions((qs) => qs.map((q, j) => (j === i ? { ...q, ...next } : q)));
  const pickExample = (ex) => {
    const next = {
      text: ex.text,
      type: ex.type,
      labelsText: ex.labels?.join(", ") || "",
    };
    setQuestions((qs) => {
      const empty = qs.findIndex((q) => !q.text.trim());
      if (empty >= 0) return qs.map((q, j) => (j === empty ? next : q));
      return qs.length < MAX_QUESTIONS ? [...qs, next] : qs;
    });
  };

  return (
    <Box sx={{ maxWidth: 760 }}>
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        Answers from saved trials. Your agent isn&apos;t run again.
      </Typography>

      <Stack spacing={2.5} sx={{ mt: 3 }}>
        <ScopePicker
          ctx={ctx}
          filter={filter}
          selectedIds={selectedIds}
          state={state}
          runLabel={runLabel}
        />

        {questions.map((q, i) => {
          const labels = labelsFrom(q.labelsText);
          return (
            <Box key={i}>
              <Stack direction="row" alignItems="center">
                <FieldLabel>Question {i + 1}</FieldLabel>
                <Box sx={{ flex: 1 }} />
                {questions.length > 1 && (
                  <IconButton
                    size="small"
                    aria-label={`Remove question ${i + 1}`}
                    onClick={() =>
                      setQuestions((qs) => qs.filter((_, j) => j !== i))
                    }
                    sx={{ mb: 0.75 }}
                  >
                    <Iconify icon="solar:close-linear" width={14} />
                  </IconButton>
                )}
              </Stack>
              <TextField
                fullWidth
                multiline
                minRows={2}
                size="small"
                placeholder="Did the agent explain its decision clearly?"
                value={q.text}
                onChange={(e) => setQ(i, { text: e.target.value })}
              />
              <Stack
                direction="column"
                alignItems="flex-start"
                spacing={1}
                sx={{ mt: 1 }}
              >
                <SegmentedTabs
                  value={q.type}
                  onChange={(_, v) => setQ(i, { type: v })}
                >
                  <Tab value={ANSWER_TYPES.YES_NO} label="Yes / No" />
                  <Tab value={ANSWER_TYPES.CATEGORY} label="Categories" />
                </SegmentedTabs>
                {q.type === ANSWER_TYPES.CATEGORY && (
                  <TextField
                    size="small"
                    fullWidth
                    placeholder="Resolved, Handed to a person, Caller gave up, Other"
                    value={q.labelsText}
                    onChange={(e) => setQ(i, { labelsText: e.target.value })}
                    error={!!q.text.trim() && labels.length < 2}
                    helperText={
                      q.text.trim() && labels.length < 2
                        ? "Add at least two categories, separated by commas"
                        : `Separated by commas · up to ${MAX_CATEGORIES}`
                    }
                  />
                )}
              </Stack>
            </Box>
          );
        })}

        {questions.length < MAX_QUESTIONS && (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="solar:add-linear" width={14} />}
              onClick={() => setQuestions((qs) => [...qs, blank()])}
              sx={{ color: "text.primary" }}
            >
              Add another question
            </Button>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Up to {MAX_QUESTIONS} in one pass
            </Typography>
          </Stack>
        )}

        <Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 500,
              color: "text.tertiary",
              mb: 0.75,
            }}
          >
            Try one
          </Typography>
          <Stack direction="row" flexWrap="wrap" gap={0.75}>
            {EXAMPLES.map((ex) => (
              <Chip
                key={ex.text}
                size="small"
                variant="outlined"
                label={ex.text}
                onClick={() => pickExample(ex)}
              />
            ))}
          </Stack>
        </Box>

        <Box>
          <FieldLabel>Reads</FieldLabel>
          <TextField
            select
            size="small"
            value={reads}
            onChange={(e) => setReads(e.target.value)}
            sx={{ minWidth: 280 }}
          >
            {READS.map((r) => (
              <MenuItem key={r.value} value={r.value}>
                {r.label}
              </MenuItem>
            ))}
          </TextField>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            {READS.find((r) => r.value === reads)?.about}
          </Typography>
        </Box>

        <Box sx={{ pt: 1 }}>
          <ScopeReview ctx={ctx} state={state} />
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 2, mb: 1.5 }}
          >
            Extra to the run&apos;s task checks ·{" "}
            {aboutCredits(estimateOf(readable, Math.max(1, ready.length)))}.
          </Typography>
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Button
              variant="contained"
              color="primary"
              disabled={!ready.length || invalid || !readable}
              onClick={() =>
                onAsk({
                  questions: ready.map((q) => ({
                    text: q.text,
                    type: q.type,
                    labels: labelsFrom(q.labelsText),
                  })),
                  scope,
                  scopeLabel,
                  trials,
                  reads,
                })
              }
            >
              Evaluate {readable} {readable === 1 ? "trial" : "trials"}
            </Button>
            {onCancel && (
              <Button onClick={onCancel} sx={{ color: "text.secondary" }}>
                Cancel
              </Button>
            )}
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {Math.max(1, ready.length)}{" "}
              {ready.length > 1 ? "questions" : "question"}
            </Typography>
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}
AskTrialsComposer.propTypes = {
  ctx: PropTypes.object.isRequired,
  filter: PropTypes.object,
  selectedIds: PropTypes.array,
  runLabel: PropTypes.string.isRequired,
  initial: PropTypes.object,
  onCancel: PropTypes.func,
  onAsk: PropTypes.func.isRequired,
};
