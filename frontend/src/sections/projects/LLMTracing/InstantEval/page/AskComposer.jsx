import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  MenuItem,
  Paper,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import QuestionEditor from "../QuestionEditor";
import {
  MAX_QUESTIONS,
  readOption,
  readsFor,
  validateInstantEval,
} from "../instantEvalPayloads";
import {
  LIMIT_OPTIONS,
  defaultReads,
  draftsFromQuestions,
  emptyDraft,
  filtersWindow,
  formatCount,
  limitHelp,
  limitOptionLabel,
  runButtonLabel,
  unitMeta,
} from "../instantEvalUnits";
import { useInstantEvals } from "../useInstantEval";
import { useJevAvailability } from "../useJevAvailability";
import { EXAMPLES } from "./askExamples";
import { autoLimit, useAskPopulation } from "./askPopulation";
import { sourceViewLink, sourceViewTitle } from "./askLinks";
import { scopeWords } from "./askFormat";

const asDraft = (entry) => draftsFromQuestions([entry])[0];

function ScopeCard({ projectId, unit, seed, population, limit }) {
  const meta = unitMeta(unit);
  // The view's own words for its scope, filters counted.
  const scope = scopeWords(seed?.label);
  const size = population.count;
  const asked = size ? Math.min(limit, size.count) : 0;
  return (
    <Paper
      variant="outlined"
      sx={{ p: 2, borderRadius: 1.5, bgcolor: "background.neutral" }}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Iconify
          icon="mdi:table-eye"
          width={20}
          sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {population.isLoading ? (
            <Skeleton variant="text" width={260} />
          ) : population.isError ? (
            <Typography variant="body2" color="error.main">
              The {meta.plural} in that view couldn&apos;t be read.{" "}
              <Link
                component="button"
                type="button"
                onClick={() => population.refetch()}
              >
                Retry
              </Link>
            </Typography>
          ) : (
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {formatCount(size?.count, size?.lowerBound)}{" "}
              {size?.count === 1 ? meta.noun : meta.plural}
              {population.handedOver
                ? ` handed over${scope ? ` from ${scope}` : ""}`
                : ` in the ${sourceViewTitle(unit)} view`}
              {asked && size && asked < size.count
                ? ` · Jev reads the newest ${formatCount(asked)}`
                : ""}
            </Typography>
          )}
          <Typography variant="caption" color="text.secondary" component="p">
            {population.handedOver
              ? "Exactly the rows handed over."
              : scope || "The rows the view shows, newest first."}{" "}
            <Link
              component={RouterLink}
              to={sourceViewLink(projectId, unit)}
              underline="hover"
            >
              Change them in {sourceViewTitle(unit)}
            </Link>
          </Typography>
        </Box>
      </Stack>
    </Paper>
  );
}

ScopeCard.propTypes = {
  projectId: PropTypes.string.isRequired,
  unit: PropTypes.string.isRequired,
  seed: PropTypes.object,
  population: PropTypes.object.isRequired,
  limit: PropTypes.number.isRequired,
};

/**
 * A new question, full page: what Jev should answer about every row handed
 * over from a view, what it reads of each, and how many rows. Asking starts
 * the run at once; the page then shows every row filling in.
 */
export default function AskComposer({
  projectId,
  unit,
  seed,
  starting,
  error,
  onAsk,
}) {
  const meta = unitMeta(unit);
  const jev = useJevAvailability();
  const population = useAskPopulation({ projectId, unit, seed });
  const saved = useInstantEvals(projectId, { unit });
  const [questions, setQuestions] = useState(() =>
    seed?.questions?.length
      ? draftsFromQuestions(seed.questions)
      : [emptyDraft()],
  );
  const [reads, setReads] = useState(() => defaultReads(unit));
  // Until a size is picked, the run covers the whole view (up to the largest
  // size), so the timeline shows the whole window.
  const [pickedLimit, setLimit] = useState(null);
  const [touched, setTouched] = useState(false);
  const errors = questions.map((question) =>
    validateInstantEval(question, meta.noun),
  );
  const readChoices = readsFor(unit);
  const limitChoices = population.handedOver
    ? [population.items.length]
    : LIMIT_OPTIONS[unit] || LIMIT_OPTIONS.traces;
  const limit =
    pickedLimit ??
    (population.handedOver
      ? population.items.length
      : autoLimit(limitChoices, population.count?.count, unit));
  const coverage = population.count
    ? Math.min(limit, population.count.count)
    : limit;

  const addQuestion = (entry) =>
    setQuestions((prev) => {
      const draft = asDraft(entry);
      const key = draft.question.trim().toLowerCase();
      if (prev.some((q) => q.question.trim().toLowerCase() === key))
        return prev;
      const blank = prev.findIndex((question) => !question.question.trim());
      if (blank >= 0) return prev.map((q, i) => (i === blank ? draft : q));
      if (prev.length >= MAX_QUESTIONS) return prev;
      return [...prev, draft];
    });

  const ask = () => {
    setTouched(true);
    if (errors.some(Boolean) || !population.items.length) return;
    onAsk({
      questions,
      reads,
      items: population.items.slice(0, limit),
      filters: seed?.filters || [],
      handedOver: population.handedOver,
      window: filtersWindow(seed?.filters) || null,
    });
  };

  const showExamples = !questions.some((question) => question.question.trim());
  const recent = (saved.items || []).slice(0, 6);
  const nothingInView =
    !population.isLoading && !population.isError && !population.items.length;

  return (
    <Stack spacing={3} sx={{ maxWidth: 880 }}>
      <Box>
        <Typography
          variant="h4"
          component="h1"
          sx={{ fontSize: 22, lineHeight: "30px", fontWeight: 600 }}
        >
          Ask Jev about these {meta.plural}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Jev reads every {meta.noun} and answers your question for each, in
          seconds. The answers land on a timeline as they come in.
        </Typography>
      </Box>

      {!jev.isLoading && !jev.available && (
        <Alert
          severity="info"
          action={
            jev.settingsPath && (
              <Button
                size="small"
                component={RouterLink}
                to={jev.settingsPath}
                sx={{ textTransform: "none" }}
              >
                Open AI Providers
              </Button>
            )
          }
        >
          {jev.reason}
        </Alert>
      )}

      <ScopeCard
        projectId={projectId}
        unit={unit}
        seed={seed}
        population={population}
        limit={limit}
      />

      <Stack spacing={1.25}>
        {questions.map((value, index) => (
          <QuestionEditor
            key={index}
            index={index}
            value={value}
            autoFocus={index === questions.length - 1}
            placeholder={EXAMPLES[unit]?.[0]?.question}
            error={touched ? errors[index] : null}
            onChange={(next) =>
              setQuestions((prev) =>
                prev.map((q, i) => (i === index ? next : q)),
              )
            }
            onRemove={
              questions.length > 1
                ? () =>
                    setQuestions((prev) => prev.filter((_, i) => i !== index))
                : undefined
            }
          />
        ))}
        <Stack direction="row" alignItems="center" spacing={1}>
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:plus" width={16} />}
            disabled={questions.length >= MAX_QUESTIONS}
            onClick={() => setQuestions((prev) => [...prev, emptyDraft()])}
          >
            Add another question
          </Button>
          <Typography variant="caption" color="text.secondary">
            Up to {MAX_QUESTIONS} in one pass
          </Typography>
        </Stack>
      </Stack>

      {showExamples && (
        <Box>
          <Typography
            variant="overline"
            color="text.secondary"
            component="h2"
            sx={{ display: "block", mb: 0.75 }}
          >
            Try one
          </Typography>
          <Stack direction="row" gap={0.75} flexWrap="wrap">
            {(EXAMPLES[unit] || EXAMPLES.traces).map((example) => (
              <Chip
                key={example.question}
                size="small"
                variant="outlined"
                label={example.question}
                onClick={() => addQuestion(example)}
                sx={{
                  maxWidth: "100%",
                  height: "auto",
                  "& .MuiChip-label": { whiteSpace: "normal", py: 0.5 },
                }}
              />
            ))}
          </Stack>
        </Box>
      )}

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 2,
          alignItems: "start",
        }}
      >
        <TextField
          select
          size="small"
          label="Jev reads"
          value={reads}
          onChange={(event) => setReads(event.target.value)}
          helperText={readOption(unit, reads).hint}
          SelectProps={{
            renderValue: (value) => readOption(unit, value).label,
          }}
        >
          {readChoices.map((value) => (
            <MenuItem key={value} value={value} sx={{ display: "block" }}>
              <Typography variant="body2">
                {readOption(unit, value).label}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                component="p"
                sx={{ whiteSpace: "normal" }}
              >
                {readOption(unit, value).hint}
              </Typography>
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label={unit === "users" ? "Users" : meta.title}
          value={limitChoices.includes(limit) ? limit : ""}
          onChange={(event) => setLimit(Number(event.target.value))}
          helperText={
            population.handedOver
              ? "Every row handed over."
              : limitHelp({ unit, limit })
          }
          disabled={population.handedOver}
        >
          {limitChoices.map((value) => (
            <MenuItem key={value} value={value}>
              {population.handedOver
                ? `All ${formatCount(value)} ${unitMeta(unit).plural}`
                : limitOptionLabel(unit, value)}
            </MenuItem>
          ))}
        </TextField>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}
      {nothingInView && (
        <Alert severity="info">
          There are no {meta.plural} in that view to ask about. Widen its date
          range or filters in {sourceViewTitle(unit)}.
        </Alert>
      )}

      <Box>
        <Button
          size="large"
          variant="contained"
          disabled={
            !jev.available ||
            jev.isLoading ||
            starting ||
            population.isLoading ||
            nothingInView
          }
          onClick={ask}
          startIcon={
            starting ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Iconify icon="mdi:creation-outline" width={18} />
            )
          }
        >
          {starting
            ? "Starting…"
            : population.isLoading
              ? `Counting the ${meta.plural}…`
              : runButtonLabel({ unit, coverage })}
        </Button>
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ mt: 1, maxWidth: 640 }}
        >
          Each question is saved as a Jev evaluator you can reuse or edit. Every
          answer is billed as one eval run; TypeSafe bills its tokens to your
          own key.
        </Typography>
      </Box>

      {recent.length > 0 && (
        <Box>
          <Typography
            variant="overline"
            color="text.secondary"
            component="h2"
            sx={{ display: "block", mb: 0.75 }}
          >
            Recent questions
          </Typography>
          <Stack direction="row" gap={0.75} flexWrap="wrap">
            {recent.map((item) => (
              <Chip
                key={item.configId}
                size="small"
                variant="outlined"
                icon={<Iconify icon="mdi:history" width={14} />}
                label={item.question}
                title={item.question}
                onClick={() => {
                  addQuestion({
                    question: item.question,
                    answerType: item.answerType,
                    categories: item.labels,
                    definitions: item.definitions,
                  });
                  if (readChoices.includes(item.reads)) setReads(item.reads);
                }}
                sx={{ maxWidth: "100%" }}
              />
            ))}
          </Stack>
        </Box>
      )}
    </Stack>
  );
}

AskComposer.propTypes = {
  projectId: PropTypes.string.isRequired,
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]).isRequired,
  seed: PropTypes.object,
  starting: PropTypes.bool,
  error: PropTypes.string,
  onAsk: PropTypes.func.isRequired,
};
