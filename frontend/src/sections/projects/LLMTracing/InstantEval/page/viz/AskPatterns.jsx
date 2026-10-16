import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { formatCount, formatShare } from "./askAnswers";
import { MIN_ROWS, Q_MAX, findPatterns } from "./askStats";
import { VizCard, quoted, useAnswerColor } from "./vizParts";

// Patterns are only worth computing once a few rows are in.
export const MIN_ANSWERED = 12;

const DIMENSION_ICON = {
  day: "mdi:calendar-blank-outline",
  window: "mdi:clock-outline",
  user: "mdi:account-outline",
  users: "mdi:account-multiple-outline",
  model: "mdi:chip",
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

/**
 * What stands out for the answer in focus: the groups of rows (a day, a
 * stretch of an afternoon, a user, a model, a turn count, errors, another
 * question's answer) where it is clearly more or less common than elsewhere.
 * Each callout is counts only, and "Show n" selects the group.
 */
export default function AskPatterns({
  records,
  questions,
  focus,
  onFocus,
  onSelect,
  running,
  unit: unitProp,
}) {
  const colorOf = useAnswerColor();
  const unit = unitProp || records[0]?.unit || "sessions";
  const result = useMemo(
    () => findPatterns({ records, questions, focus, unit }),
    [records, questions, focus, unit],
  );
  const { question, answer, patterns } = result;
  if (!question) return null;
  const color = colorOf(question, answer);
  const tooFew = result.answered < MIN_ANSWERED;

  const caption = (
    <>
      Where {quoted(answer)} to {quoted(question.question)} is clearly more or
      less common than in the rest
      {result.answered > 0 &&
        ` (${formatShare(result.rate)} of ${formatCount(result.answered)} answered)`}
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
        Nothing stands out{running ? " yet" : ""}: no day, time, user, model,
        turn count, error or other answer has a clearly different share of{" "}
        {quoted(answer)} after checking {formatCount(result.tested)} groups.
      </Typography>
    );
  else
    body = (
      <Box component="ol" sx={{ m: 0, p: 0, listStyle: "none" }}>
        {patterns.map((pattern) => (
          <Stack
            key={pattern.key}
            component="li"
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{
              py: 1,
              "&:not(:last-of-type)": {
                borderBottom: "1px solid",
                borderColor: "divider",
              },
            }}
          >
            <Iconify
              icon={DIMENSION_ICON[pattern.dimension] || "mdi:shape-outline"}
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Tooltip
              arrow
              placement="top-start"
              describeChild
              title={`Holds after correcting for ${formatCount(result.counted)} comparisons (${qText(pattern.q)}). Groups need at least ${MIN_ROWS} answered rows and a gap of 10 points.`}
            >
              <Typography
                variant="body2"
                sx={{
                  flex: 1,
                  minWidth: 0,
                  fontSize: 13,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                <Box component="span" sx={{ fontWeight: 600 }}>
                  {quoted(answer)} {pattern.where}
                </Box>
                : {pattern.k} of {pattern.n} ({formatShare(pattern.rate)}) vs{" "}
                {formatShare(pattern.restRate)} elsewhere
                {pattern.when && (
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    , {pattern.when}
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
              onClick={() =>
                onSelect?.(pattern.ids, `${quoted(answer)} ${pattern.where}`)
              }
              sx={{
                flexShrink: 0,
                fontSize: 12,
                py: 0.25,
                minWidth: 88,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              Show {formatCount(pattern.n)}
            </Button>
          </Stack>
        ))}
      </Box>
    );

  return (
    <VizCard
      title="What stands out"
      label="Patterns"
      caption={caption}
      action={
        question.labels?.length > 0 && (
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
        )
      }
    >
      {body}
      {!tooFew && patterns.length > 0 && (
        <Typography
          variant="caption"
          color="text.disabled"
          component="p"
          sx={{ m: 0, mt: 0.75 }}
        >
          Each holds after correcting for {formatCount(result.counted)}{" "}
          comparisons: {formatCount(result.tested)} groups checked, less the
          ones too small to ever stand out (Fisher&apos;s exact test, q &lt;{" "}
          {Q_MAX}).
          {running ? " Updates as answers arrive." : ""}
        </Typography>
      )}
    </VizCard>
  );
}

AskPatterns.propTypes = {
  records: PropTypes.array.isRequired,
  questions: PropTypes.array.isRequired,
  focus: PropTypes.shape({
    configId: PropTypes.string,
    answer: PropTypes.string,
  }),
  onFocus: PropTypes.func,
  onSelect: PropTypes.func,
  onOpen: PropTypes.func,
  running: PropTypes.bool,
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]),
};
