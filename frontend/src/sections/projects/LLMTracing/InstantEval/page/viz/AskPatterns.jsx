import React, { useDeferredValue, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { formatCount, formatShare, isYesNo } from "./askAnswers";
import { chartQuestions, runTally } from "./askSummary";
import {
  MIN_CONSISTENT_SESSIONS,
  MIN_ROWS,
  Q_MAX,
  findConsistency,
  findPatterns,
} from "./askStats";
import {
  Swatch,
  VizCard,
  quoted,
  sampleText,
  useAnswerColor,
  useRunData,
  useThrottled,
} from "./vizParts";

// Patterns are only worth computing once a few rows are in.
export const MIN_ANSWERED = 12;
// While answers arrive, patterns are recomputed at most this often (the
// charts take every poll).
export const PATTERNS_EVERY_MS = 10000;
// "Show all" lists this many at a time.
const MORE_PAGE = 50;
const ALL = "__all__";

const DIMENSION_ICON = {
  day: "mdi:calendar-blank-outline",
  window: "mdi:clock-outline",
  weekday: "mdi:calendar-week-outline",
  weekpart: "mdi:calendar-weekend-outline",
  tod: "mdi:clock-time-four-outline",
  user: "mdi:account-outline",
  users: "mdi:account-multiple-outline",
  consistent: "mdi:account-multiple-check-outline",
  model: "mdi:chip",
  modelsince: "mdi:chip",
  turns: "mdi:message-reply-text-outline",
  error: "mdi:alert-circle-outline",
  question: "mdi:help-circle-outline",
};

const qText = (q) =>
  q < 0.001 ? "q < 0.001" : `q = ${q.toFixed(q < 0.01 ? 3 : 2)}`;

// The group's share against the rest, on one 0–100% track.
function ShareTrack({ rate, restRate, color }) {
  const theme = useTheme();
  return (
    <Box
      aria-hidden
      sx={{
        position: "relative",
        width: 72,
        height: 6,
        borderRadius: 3,
        bgcolor: "action.hover",
        flexShrink: 0,
      }}
    >
      <Box
        sx={{
          position: "absolute",
          inset: 0,
          width: `${rate * 100}%`,
          borderRadius: 3,
          bgcolor: color,
        }}
      />
      <Box
        sx={{
          position: "absolute",
          top: -3,
          bottom: -3,
          left: `calc(${restRate * 100}% - 1px)`,
          width: 2,
          borderRadius: 1,
          bgcolor: theme.palette.text.primary,
          boxShadow: `0 0 0 1.5px ${theme.palette.background.paper}`,
        }}
      />
    </Box>
  );
}

ShareTrack.propTypes = {
  rate: PropTypes.number.isRequired,
  restRate: PropTypes.number.isRequired,
  color: PropTypes.string,
};

// One callout: the group's answer in counts against the rest, its share on a
// track, and "Show" for its rows. `dense` rows sit under "Show all".
function PatternRow({
  pattern,
  color,
  showSwatch,
  inSample,
  counted,
  onSelect,
  dense,
}) {
  const head = `${quoted(pattern.label)} ${pattern.where}`;
  const fontSize = dense ? 12 : 13;
  return (
    <Stack
      component="li"
      direction="row"
      spacing={1.5}
      alignItems="center"
      data-pattern={pattern.key}
      sx={{
        py: dense ? 0.5 : 1,
        "&:not(:last-of-type)": {
          borderBottom: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Iconify
        icon={DIMENSION_ICON[pattern.dimension] || "mdi:shape-outline"}
        width={dense ? 14 : 16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Tooltip
        arrow
        placement="top-start"
        describeChild
        title={`Holds after correcting for ${formatCount(counted)} comparisons (${qText(pattern.q)}). Groups need at least ${MIN_ROWS} answered rows and a gap of 10 points (5 where the share doubles or halves).`}
      >
        <Typography
          variant="body2"
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {showSwatch && (
            <Swatch
              color={color}
              size={9}
              sx={{ display: "inline-block", mr: 0.75 }}
            />
          )}
          <Box component="span" sx={{ fontWeight: dense ? 500 : 600 }}>
            {head}
          </Box>
          : {formatCount(pattern.k)} of {formatCount(pattern.n)} (
          {formatShare(pattern.rate)}) vs {formatShare(pattern.restRate)}{" "}
          {pattern.restText}
          {inSample}
          {pattern.when && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              , {pattern.when}
            </Box>
          )}
          {pattern.within && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {" "}
              · part of {quoted(pattern.within.label || pattern.label)}{" "}
              {pattern.within.where}
            </Box>
          )}
        </Typography>
      </Tooltip>
      <ShareTrack
        rate={pattern.rate}
        restRate={pattern.restRate}
        color={color}
      />
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => onSelect?.(pattern.select.filter, pattern.select.label)}
        sx={{
          flexShrink: 0,
          fontSize: 12,
          py: dense ? 0 : 0.25,
          minWidth: 88,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        Show {formatCount(pattern.select.count)}
      </Button>
    </Stack>
  );
}

PatternRow.propTypes = {
  pattern: PropTypes.object.isRequired,
  color: PropTypes.string,
  showSwatch: PropTypes.bool,
  inSample: PropTypes.string,
  counted: PropTypes.number,
  onSelect: PropTypes.func,
  dense: PropTypes.bool,
};

// A user question's users who kept getting an answer: how many had it in
// every session read (and in all but one), against what chance would give.
function ConsistencyRow({ line, color, showSwatch, onSelect }) {
  return (
    <Stack
      component="li"
      direction="row"
      spacing={1.5}
      alignItems="center"
      data-consistency={line.label}
      sx={{
        py: 1,
        "&:not(:last-of-type)": {
          borderBottom: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Iconify
        icon={DIMENSION_ICON.consistent}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Tooltip
          arrow
          placement="top-start"
          describeChild
          title={`More users than chance alone would give (${qText(line.q)}). Users with at least ${MIN_CONSISTENT_SESSIONS} sessions answered count; a tier counts when its share is surely above the run's.`}
        >
          <Typography
            variant="body2"
            sx={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}
          >
            {showSwatch && (
              <Swatch
                color={color}
                size={9}
                sx={{ display: "inline-block", mr: 0.75 }}
              />
            )}
            <Box component="span" sx={{ fontWeight: 600 }}>
              {line.text}
            </Box>
          </Typography>
        </Tooltip>
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ m: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {line.chance}
        </Typography>
      </Box>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => onSelect?.(line.select.filter, line.select.label)}
        sx={{
          flexShrink: 0,
          fontSize: 12,
          py: 0.25,
          minWidth: 88,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        Show {formatCount(line.select.count)}
      </Button>
    </Stack>
  );
}

ConsistencyRow.propTypes = {
  line: PropTypes.object.isRequired,
  color: PropTypes.string,
  showSwatch: PropTypes.bool,
  onSelect: PropTypes.func,
};

/**
 * What stands out: the groups of rows (a day, a stretch of an afternoon, a
 * weekday, the weekend, a time of day, a user, a model, a turn count, errors,
 * another question's answer) where an answer is clearly more or less common
 * than elsewhere. A Yes/No question looks at "No" (or the answer in focus); a
 * category question with nothing in focus looks at every label at once.
 * Each callout is counts only, and "Show" selects the group. The top five
 * are shown, at most two of a kind; "Show all" lists every one that passed,
 * with the ones that sit inside a stronger callout saying so. A user
 * question first lists the users who kept getting an answer (in every
 * session read, or all but one) when there are clearly more of them than
 * chance would give. While the run is answering, they are recomputed at
 * most every 10 seconds.
 */
export default function AskPatterns({
  summary: given,
  records,
  questions: questionList,
  focus,
  onFocus,
  onSelect,
  running,
  window,
  unit: unitProp,
}) {
  const colorOf = useAnswerColor();
  const { summary, questions, sample, unit } = useRunData({
    summary: given,
    records,
    questions: questionList,
    window,
    unit: unitProp,
    running,
    defer: true,
  });
  // A new answer in focus is looked at in a background render, so the
  // timeline and legend answer the click at once.
  const shownFocus = useDeferredValue(focus);
  // Recomputed at most every 10 seconds while answers arrive, and at once
  // for a new question or answer in focus.
  const focusKey = `${shownFocus?.configId || ""}|${shownFocus?.answer || ""}`;
  // Until enough rows are answered there are no patterns to recompute, only
  // the count so far: each new count is taken at once, and so is the first
  // count with enough rows, so patterns appear as soon as they can.
  const answeredNow = runTally(
    summary,
    questions.find((item) => item.configId === shownFocus?.configId) ||
      questions[0],
  ).answered;
  const gathering = answeredNow < MIN_ANSWERED ? String(answeredNow) : "enough";
  const counts = useThrottled(
    summary,
    running ? PATTERNS_EVERY_MS : 0,
    `${focusKey}|${gathering}`,
  );
  const result = useMemo(
    () =>
      findPatterns({
        summary: counts,
        questions: chartQuestions(questionList, counts),
        focus: shownFocus,
        unit,
      }),
    [counts, questionList, shownFocus, unit],
  );
  const consistency = useMemo(
    () =>
      unit === "users" && result.question
        ? findConsistency({
            summary: counts,
            question: result.question,
            answer: result.answer,
          })
        : [],
    [counts, result.question, result.answer, unit],
  );
  const [moreShown, setMoreShown] = useState(0);
  const { question, answer, patterns, more } = result;
  if (!question || !questions.length) return null;
  const everyLabel = !answer;
  const tooFew = result.answered < MIN_ANSWERED;
  const inSample = sample.isSample ? " in the sample" : "";
  const rowProps = (pattern) => ({
    pattern,
    color: colorOf(question, pattern.label),
    showSwatch: everyLabel,
    inSample,
    counted: result.counted,
    onSelect,
  });

  // The caption says what the callouts below show; with none to show, it
  // only gives the share, so it never reads as a finding.
  const found = !tooFew && patterns.length > 0;
  const caption = !found ? (
    everyLabel ? (
      <>
        Answers to {quoted(question.question)}
        {result.answered > 0 &&
          ` (${formatCount(result.answered)} answered${inSample})`}
        .
      </>
    ) : (
      <>
        {quoted(answer)} to {quoted(question.question)}
        {result.answered > 0 &&
          `: ${formatShare(result.rate)} of ${formatCount(result.answered)} answered${inSample}`}
        .
      </>
    )
  ) : everyLabel ? (
    <>
      Where any answer to {quoted(question.question)} is clearly more or less
      common than in the rest
      {result.answered > 0 &&
        ` (${formatCount(result.answered)} answered${inSample})`}
      .
    </>
  ) : (
    <>
      Where {quoted(answer)} to {quoted(question.question)} is clearly more or
      less common than in the rest
      {result.answered > 0 &&
        ` (${formatShare(result.rate)} of ${formatCount(result.answered)} answered${inSample})`}
      .
    </>
  );

  let body;
  if (tooFew)
    body = (
      <Typography variant="body2" color="text.secondary" sx={{ fontSize: 13 }}>
        {running
          ? `Patterns appear once ${MIN_ANSWERED} rows are answered (${formatCount(result.answered)} so far).`
          : `Too few answers to compare groups (${formatCount(result.answered)}).`}
      </Typography>
    );
  else if (!patterns.length)
    body = (
      <Typography variant="body2" color="text.secondary" sx={{ fontSize: 13 }}>
        {consistency.length ? "Nothing else stands out" : "Nothing stands out"}
        {running ? " yet" : ""}: no day, time, user, model, turn count, error or
        other answer has a clearly different share of{" "}
        {everyLabel ? "any answer" : quoted(answer)} after checking{" "}
        {formatCount(result.tested)} groups.
      </Typography>
    );
  else
    body = (
      <>
        <Box
          component="ol"
          aria-label="Strongest patterns"
          sx={{ m: 0, p: 0, listStyle: "none" }}
        >
          {patterns.map((pattern) => (
            <PatternRow key={pattern.key} {...rowProps(pattern)} />
          ))}
        </Box>
        {more.length > 0 && (
          <Box sx={{ mt: 0.5 }}>
            {moreShown > 0 && (
              <Box
                component="ol"
                aria-label="More patterns"
                start={patterns.length + 1}
                sx={{
                  m: 0,
                  mt: 0.5,
                  p: 0,
                  pl: 1.5,
                  listStyle: "none",
                  borderLeft: "2px solid",
                  borderColor: "divider",
                }}
              >
                {more.slice(0, moreShown).map((pattern) => (
                  <PatternRow key={pattern.key} dense {...rowProps(pattern)} />
                ))}
              </Box>
            )}
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
              {moreShown < more.length && (
                <Button
                  size="small"
                  color="inherit"
                  aria-expanded={moreShown > 0}
                  onClick={() => setMoreShown((shown) => shown + MORE_PAGE)}
                  startIcon={<Iconify icon="mdi:chevron-down" width={16} />}
                  sx={{ fontSize: 12, color: "text.secondary" }}
                >
                  {moreShown === 0
                    ? `Show all ${formatCount(patterns.length + more.length)}`
                    : `Show ${formatCount(Math.min(MORE_PAGE, more.length - moreShown))} more`}
                </Button>
              )}
              {moreShown > 0 && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setMoreShown(0)}
                  startIcon={<Iconify icon="mdi:chevron-up" width={16} />}
                  sx={{ fontSize: 12, color: "text.secondary" }}
                >
                  Top {formatCount(patterns.length)} only
                </Button>
              )}
            </Stack>
          </Box>
        )}
      </>
    );

  return (
    <VizCard
      title="What stands out"
      label="Patterns"
      caption={caption}
      action={
        isYesNo(question) ? (
          <Stack direction="row" spacing={0.5} alignItems="center">
            {question.labels.map((label) => {
              const active = label === answer;
              return (
                <Button
                  key={label}
                  size="small"
                  color="inherit"
                  aria-pressed={active}
                  onClick={() =>
                    onFocus?.({ configId: question.configId, answer: label })
                  }
                  sx={{
                    fontSize: 12,
                    fontWeight: active ? 600 : 400,
                    py: 0.25,
                    minWidth: 0,
                    px: 1,
                    border: "1px solid",
                    borderColor: active ? "text.primary" : "divider",
                    color: active ? "text.primary" : "text.secondary",
                  }}
                >
                  {label}
                </Button>
              );
            })}
          </Stack>
        ) : (
          <TextField
            select
            size="small"
            label="Answer"
            value={answer || ALL}
            onChange={(event) =>
              onFocus?.({
                configId: question.configId,
                answer: event.target.value === ALL ? null : event.target.value,
              })
            }
            sx={{ width: 220 }}
          >
            <MenuItem value={ALL}>All answers</MenuItem>
            {question.labels.map((label) => (
              <MenuItem key={label} value={label}>
                <Swatch
                  color={colorOf(question, label)}
                  size={9}
                  sx={{ mr: 1 }}
                />
                {label}
              </MenuItem>
            ))}
          </TextField>
        )
      }
    >
      {!tooFew && consistency.length > 0 && (
        <Box
          component="ul"
          aria-label="Users who kept getting an answer"
          sx={{
            m: 0,
            mb: 0.5,
            p: 0,
            listStyle: "none",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {consistency.map((line) => (
            <ConsistencyRow
              key={line.key}
              line={line}
              color={colorOf(question, line.label)}
              showSwatch={everyLabel}
              onSelect={onSelect}
            />
          ))}
        </Box>
      )}
      {body}
      {!tooFew && patterns.length > 0 && (
        <Typography
          variant="caption"
          color="text.disabled"
          component="p"
          sx={{ m: 0, mt: 0.75 }}
        >
          Each holds after correcting for {formatCount(result.counted)}{" "}
          comparisons: {formatCount(result.tested)} checked
          {everyLabel ? ` across ${question.labels.length} answers` : ""}, less
          the ones too small to ever stand out (Fisher&apos;s exact test, q &lt;{" "}
          {Q_MAX}).
          {sample.isSample ? ` ${sampleText(sample, unit)}` : ""}
          {running ? " Rechecked every 10 seconds while answers arrive." : ""}
        </Typography>
      )}
    </VizCard>
  );
}

AskPatterns.propTypes = {
  summary: PropTypes.object,
  records: PropTypes.array,
  questions: PropTypes.array,
  focus: PropTypes.shape({
    configId: PropTypes.string,
    answer: PropTypes.string,
  }),
  onFocus: PropTypes.func,
  onSelect: PropTypes.func,
  onOpen: PropTypes.func,
  running: PropTypes.bool,
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]),
  window: PropTypes.array,
};
