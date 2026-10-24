import React, {
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha, darken, lighten, useTheme } from "@mui/material/styles";
import { UNITS } from "src/sections/projects/observePopulation";
import { NO_COLOR, answerColor } from "../../results/answerColors";
import { formatCount, isYesNo } from "./askAnswers";
import {
  SQUARES_MAX,
  chartQuestions,
  runTally,
  sampleOf,
  summarizeRecords,
} from "./askSummary";

// Pieces the charts share: the run's counts, the card they sit in, its
// header, and the colour of an answer.

/**
 * What every chart reads: the server's summary, or one built from the rows
 * when the page only has rows; the questions; whether it was a sample; and
 * whether there are few enough rows to draw one square each. With `defer`,
 * a new summary (the next poll) is taken up in a background render, so the
 * heavier charts never hold up the page.
 */
export function useRunData({
  summary: given,
  records,
  questions,
  window,
  unit,
  running,
  defer = false,
}) {
  const built = useMemo(
    () =>
      given
        ? null
        : summarizeRecords({
            records: records || [],
            questions: questions || [],
            window,
            unit,
            status: running ? "running" : undefined,
          }),
    [given, records, questions, window, unit, running],
  );
  const latest = given || built;
  const deferred = useDeferredValue(latest);
  const summary = defer ? deferred : latest;
  const list = useMemo(
    () => chartQuestions(questions, summary),
    [questions, summary],
  );
  const squares = Boolean(
    records?.length && (summary?.total ?? records.length) <= SQUARES_MAX,
  );
  return {
    summary,
    questions: list,
    sample: sampleOf(summary),
    squares,
    unit: unit || summary?.unit || records?.[0]?.unit || "sessions",
  };
}

/**
 * `value`, taken up at most once every `ms` (the latest one when the time
 * comes); at once when `ms` is 0 or `key` changes (a new question or answer
 * in focus).
 */
export function useThrottled(value, ms, key) {
  const [shown, setShown] = useState(value);
  const last = useRef({ at: 0, key });
  useEffect(() => {
    // A new key recomputes now, with the latest value.
    const sameKey = last.current.key === key;
    if (shown === value) {
      if (!sameKey) last.current = { at: Date.now(), key };
      return undefined;
    }
    const take = () => {
      last.current = { at: Date.now(), key };
      setShown(value);
    };
    const wait = ms && sameKey ? last.current.at + ms - Date.now() : 0;
    if (wait <= 0) {
      take();
      return undefined;
    }
    const timer = setTimeout(take, wait);
    return () => clearTimeout(timer);
  }, [value, ms, key, shown]);
  return shown;
}

/** "sessions read" for a sample, "sessions" otherwise. */
export const rowsNoun = (unit, sample, count) => {
  const { noun, plural } = UNITS[unit] || UNITS.sessions;
  const word = count === 1 ? noun : plural;
  return sample?.isSample ? `${word} read` : word;
};

/** "A uniform sample: 1,000 of 25,012 sessions read." */
export function sampleText(sample, unit) {
  if (!sample?.isSample) return "";
  const { plural } = UNITS[unit] || UNITS.sessions;
  return `A uniform sample: ${formatCount(sample.read)} of ${formatCount(sample.population)} ${plural} read, so counts are of the rows read.`;
}

/**
 * The answers to a question for a legend, from the run's counts:
 * [{answer, count, share, unsure, index}], most common first for a category
 * question (its hues stay with its labels), Yes then No otherwise.
 */
export function legendAnswers(summary, question) {
  if (!question) return [];
  const run = runTally(summary, question);
  const unsure = summary?.unsure?.[question.configId] || {};
  const entries = question.labels.map((answer, index) => ({
    answer,
    index,
    count: run.byLabel.get(answer) || 0,
    share: run.answered ? (run.byLabel.get(answer) || 0) / run.answered : 0,
    unsure: unsure[answer] || 0,
  }));
  if (isYesNo(question)) return entries;
  return entries.sort((a, b) => b.count - a.count || a.index - b.index);
}

/**
 * Width of an element, kept current as it resizes (640 where unmeasured).
 * The element may mount after the first render (a chart that waits for its
 * question), so every render checks whether it is the one being watched.
 */
export function useWidth(ref) {
  const [width, setWidth] = useState(0);
  const watched = useRef(null);
  const observer = useRef(null);
  useEffect(() => {
    const element = ref.current;
    if (element === watched.current) return;
    observer.current?.disconnect();
    observer.current = null;
    watched.current = element;
    if (!element) return;
    const measure = () =>
      setWidth(element.getBoundingClientRect().width || 640);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    observer.current = new ResizeObserver(measure);
    observer.current.observe(element);
  });
  useEffect(() => () => observer.current?.disconnect(), []);
  return width;
}

export function VizCard({ title, caption, action, children, label }) {
  return (
    <Box
      component="section"
      aria-label={label || (typeof title === "string" ? title : undefined)}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 2,
        pt: 1.75,
        pb: 1.5,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
        spacing={2}
        sx={{ mb: 1.5 }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2" component="h2" sx={{ fontSize: 15 }}>
            {title}
          </Typography>
          {caption && (
            <Typography
              variant="caption"
              color="text.secondary"
              component="p"
              sx={{ m: 0 }}
            >
              {caption}
            </Typography>
          )}
        </Box>
        {action && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ flexShrink: 0 }}
          >
            {action}
          </Stack>
        )}
      </Stack>
      {children}
    </Box>
  );
}

VizCard.propTypes = {
  title: PropTypes.node.isRequired,
  caption: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  label: PropTypes.string,
};

/** The colour of `answer` to `question`, from the results' palette. */
export function useAnswerColor() {
  const theme = useTheme();
  return (question, answer) =>
    answerColor(
      {
        answerType: question?.answerType,
        labels: question?.labels || ["Yes", "No"],
      },
      answer,
      theme,
    );
}

/**
 * The strong end of an answer's hue: burnt orange for No, a darker shade
 * else; on a dark surface, a lighter one.
 */
export const deepColor = (hue, mode = "light") =>
  mode === "dark"
    ? lighten(hue, 0.3)
    : hue === NO_COLOR
      ? "#C2410C"
      : darken(hue, 0.3);

/**
 * An answer's hue in five steps for a share from 0% to the top of a scale:
 * three tints, the hue itself, then its strong end.
 */
export const answerRamp = (hue, mode = "light") => [
  alpha(hue, mode === "dark" ? 0.16 : 0.12),
  alpha(hue, 0.35),
  alpha(hue, 0.65),
  hue,
  deepColor(hue, mode),
];

/** A quoted answer: “No”. */
export const quoted = (answer) => `“${answer}”`;

export function Swatch({ color, hollow, size = 10, sx }) {
  return (
    <Box
      component="span"
      aria-hidden
      sx={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "2px",
        flexShrink: 0,
        bgcolor: hollow ? "transparent" : color,
        border: hollow ? "1.5px solid" : "none",
        borderColor: color,
        ...sx,
      }}
    />
  );
}

Swatch.propTypes = {
  color: PropTypes.string,
  hollow: PropTypes.bool,
  size: PropTypes.number,
  sx: PropTypes.object,
};
