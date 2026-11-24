import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { CHECK_LINE_COPY as C } from "./traceTable.constants";

const SHOWN = 3;

const EVIDENCE_WORDS = {
  world_state: "the world",
  tool_calls: "the agent's calls",
  transcript: "the transcript",
  recording: "the recording",
};

const STATE = {
  failed: { icon: "solar:close-circle-linear", color: "status.danger.fg" },
  passed: { icon: "solar:check-circle-linear", color: "status.success.fg" },
  inconclusive: {
    icon: "solar:question-circle-linear",
    color: "text.tertiary",
  },
  unmeasured: { icon: "solar:minus-circle-linear", color: "text.tertiary" },
  not_applicable: { icon: "solar:minus-circle-linear", color: "text.disabled" },
  not_scored: { icon: "solar:minus-circle-linear", color: "text.tertiary" },
  waiting: { icon: "solar:clock-circle-linear", color: "text.disabled" },
};

const ORDER = [
  "failed",
  "unmeasured",
  "not_scored",
  "inconclusive",
  "waiting",
  "not_applicable",
  "passed",
];

const evidenceLabel = (evidence) =>
  `${C.from} ${evidence.map((one) => EVIDENCE_WORDS[one] || one).join(", ")}`;

function subGoalItem(goal, index) {
  const state =
    goal.result === "not_applicable" || goal.result === "unmeasured"
      ? goal.result
      : goal.passed === true
        ? "passed"
        : goal.passed === false
          ? "failed"
          : "inconclusive";
  return {
    key: `goal-${goal.name}-${index}`,
    name: goal.name,
    state,
    detail: [
      goal.evidence?.length ? evidenceLabel(goal.evidence) : null,
      goal.reason || null,
    ]
      .filter(Boolean)
      .join(". "),
  };
}

function evalItem(result) {
  const state =
    result.passed === true
      ? "passed"
      : result.passed === false
        ? "failed"
        : ["failed", "timed_out", "skipped"].includes(result.status)
          ? "not_scored"
          : "waiting";
  return {
    key: `eval-${result.id}`,
    name: result.name,
    state,
    detail: result.reason || "",
  };
}

const byState = (one, other) =>
  ORDER.indexOf(one.state) - ORDER.indexOf(other.state);

function countLine(kind, items) {
  const count = (state) => items.filter((one) => one.state === state).length;
  const failed = count("failed");
  const passed = count("passed");
  if (failed) return C.failedOf(failed, items.length);
  if (count("waiting") === items.length) return C.waitingAll(items.length);
  if (passed === items.length) return C.allPassed(kind, items.length);
  return C.passedOf(kind, passed, items.length);
}

function Row({ item }) {
  const look = STATE[item.state];
  return (
    <CustomTooltip
      show={Boolean(item.detail)}
      size="small"
      arrow
      title={item.detail}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ minWidth: 0 }}
      >
        <Box
          role="img"
          aria-label={C.state[item.state]}
          title={C.state[item.state]}
          sx={{ display: "flex", color: look.color, flexShrink: 0 }}
        >
          <Iconify icon={look.icon} width={14} aria-hidden="true" />
        </Box>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.secondary",
            minWidth: 0,
            textDecoration: item.detail ? "underline dotted" : "none",
            textUnderlineOffset: "3px",
          }}
        >
          {item.name}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

Row.propTypes = {
  item: PropTypes.shape({
    name: PropTypes.string.isRequired,
    state: PropTypes.oneOf(Object.keys(STATE)).isRequired,
    detail: PropTypes.string,
  }).isRequired,
};

function Group({ kind, items }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const sorted = [...items].sort(byState);
  const allWaiting = sorted.every((one) => one.state === "waiting");
  const shown = open ? sorted : allWaiting ? [] : sorted.slice(0, SHOWN);
  const hidden = sorted.length - shown.length;
  return (
    <Stack spacing={0.5} data-testid={`trace-check-group-${kind}`}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        <Box component="span" sx={{ fontWeight: 600, color: "text.secondary" }}>
          {C.heading[kind]}
        </Box>
        {` · ${countLine(kind, sorted)}`}
      </Typography>
      {shown.map((item) => (
        <Row key={item.key} item={item} />
      ))}
      {(hidden > 0 || open) && (
        <Link
          component="button"
          type="button"
          underline="hover"
          onClick={(event) => {
            event.stopPropagation();
            setOpen((value) => !value);
          }}
          sx={{
            typography: "s3",
            color: "text.secondary",
            alignSelf: "flex-start",
          }}
        >
          {open ? C.showLess : C.showMore(hidden)}
        </Link>
      )}
    </Stack>
  );
}

Group.propTypes = {
  kind: PropTypes.oneOf(["subGoals", "evals"]).isRequired,
  items: PropTypes.array.isRequired,
};

const hasFailure = (group) =>
  group.items.some((item) => item.state === "failed");

export default function TraceCheckLines({ subGoals = [], requiredEvals = [] }) {
  if (!subGoals.length && !requiredEvals.length) return null;
  const groups = [
    { kind: "subGoals", items: subGoals.map(subGoalItem) },
    { kind: "evals", items: requiredEvals.map(evalItem) },
  ].sort((one, other) => hasFailure(other) - hasFailure(one));
  return (
    <Stack spacing={1} sx={{ mt: 1 }} data-testid="trace-check-lines">
      {groups.map((group) => (
        <Group key={group.kind} kind={group.kind} items={group.items} />
      ))}
    </Stack>
  );
}

TraceCheckLines.propTypes = {
  subGoals: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      result: PropTypes.string,
      passed: PropTypes.bool,
      reason: PropTypes.string,
      evidence: PropTypes.arrayOf(PropTypes.string),
    }),
  ),
  requiredEvals: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      passed: PropTypes.bool,
      status: PropTypes.string,
      reason: PropTypes.string,
    }),
  ),
};
