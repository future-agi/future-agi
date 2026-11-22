import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { keyframes } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  CircularProgress,
  Button,
} from "@mui/material";

import Iconify from "src/components/iconify";
import EmptyState from "../../../../components/EmptyState";
import GoalCard, { OneOffGroup, groupOneOffs } from "./GoalCard";

// Goals and one-offs each sit in one bordered list, divided per row, instead of
// a card per item.
const listFrame = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
};

/**
 * The run's diagnosis: which goals the evals say broke, what the broken calls
 * have in common (Omega, one call at a time), and agent issues seen only once.
 */

// Shown while the investigation runs; the report arrives in one piece.
const THINKING_WORDS = [
  "Analyzing",
  "Reading transcripts",
  "Checking",
  "Debugging",
  "Tracing failures",
  "Cross-referencing calls",
  "Clustering",
  "Verifying",
];

const fadeIn = keyframes`
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: none; }
`;

// A 10-call run takes ~1.5 min. Past this, the worker is likely busy or down.
const SLOW_AFTER_MS = 4 * 60 * 1000;

function ThinkingLoader() {
  const [index, setIndex] = useState(0);
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setInterval(
      () => setIndex((i) => (i + 1) % THINKING_WORDS.length),
      1600,
    );
    const slowTimer = setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => {
      clearInterval(timer);
      clearTimeout(slowTimer);
    };
  }, []);

  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      spacing={1.5}
      sx={{ flex: 1, py: 8 }}
    >
      <CircularProgress size={22} thickness={5} />
      <Typography
        key={index}
        role="status"
        sx={{
          typography: "s2",
          fontWeight: 600,
          animation: `${fadeIn} 240ms ease-out`,
        }}
      >
        {THINKING_WORDS[index]}…
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {slow
          ? "This is taking longer than expected. You can close this and check back."
          : "Omega is reading every call in this run"}
      </Typography>
    </Stack>
  );
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function summaryOf({ summary }, visibleGoalCount) {
  if (!summary) return "No results for this run yet.";
  const { brokenGoals, brokenCalls, measuredCalls, oneOffs } = summary;
  const also = oneOffs ? `, plus ${plural(oneOffs, "one-off issue")}` : "";
  if (visibleGoalCount < brokenGoals) {
    const diagnosis = visibleGoalCount
      ? `Showing findings for ${plural(visibleGoalCount, "broken goal")}`
      : "No findings for broken goals yet";
    return `${diagnosis}${also}.`;
  }
  if (brokenGoals) {
    return `Your agent broke ${plural(brokenGoals, "goal")} on ${brokenCalls} of ${plural(
      measuredCalls,
      "call",
    )}${also}.`;
  }
  return oneOffs
    ? `Your agent held every goal on ${plural(measuredCalls, "call")}${also}.`
    : `Your agent held every goal on ${plural(measuredCalls, "call")}.`;
}

export default function DiagnosisPane({
  analysis,
  isLoading,
  isRequesting,
  onRetry,
  onViewCalls,
}) {
  if (
    isLoading ||
    isRequesting ||
    analysis.isWorking ||
    analysis.status === "not_requested"
  ) {
    return <ThinkingLoader />;
  }

  if (analysis.status === "failed") {
    return (
      <Box sx={{ flex: 1 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Couldn't finish the diagnosis"
          body={analysis.errorMessage || "Couldn't read this run. Try again."}
          action={
            <Button variant="outlined" size="small" onClick={onRetry}>
              Try again
            </Button>
          }
        />
      </Box>
    );
  }

  const goalsWithFindings = analysis.goals.filter(
    (goal) => goal.ways?.length > 0,
  );

  const oneOffGroups = groupOneOffs(analysis.oneOffs);

  return (
    <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0, px: 2.5, py: 2 }}>
      <Box
        sx={{
          px: 1.75,
          py: 1.5,
          borderRadius: "8px",
          bgcolor: "status.discovery.bg",
        }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1.25}>
          <Iconify
            icon="solar:lightbulb-bolt-linear"
            width={15}
            sx={{ color: "status.discovery.solid", flexShrink: 0, mt: "2px" }}
          />
          <Box>
            <Typography sx={{ typography: "s2" }}>
              {summaryOf(analysis, goalsWithFindings.length)}
            </Typography>
            {analysis.groupingPending && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
              >
                Still grouping how each goal broke; the breakdown may change.
              </Typography>
            )}
          </Box>
        </Stack>
      </Box>

      {goalsWithFindings.length > 0 && (
        <Box sx={{ ...listFrame, mt: 2 }}>
          {goalsWithFindings.map((goal) => (
            <GoalCard key={goal.goal} goal={goal} onViewCalls={onViewCalls} />
          ))}
        </Box>
      )}

      {oneOffGroups.length > 0 && (
        <Box sx={{ mt: 2.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>
            Also seen, 1 call each
          </Typography>
          <Box sx={{ ...listFrame, py: 0.5 }}>
            {oneOffGroups.map((group) => (
              <OneOffGroup
                key={group.phrase}
                phrase={group.phrase}
                ways={group.ways}
                onViewCalls={onViewCalls}
              />
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
}

DiagnosisPane.propTypes = {
  analysis: PropTypes.shape({
    status: PropTypes.string,
    isWorking: PropTypes.bool,
    errorMessage: PropTypes.string,
    groupingPending: PropTypes.bool,
    summary: PropTypes.shape({
      measuredCalls: PropTypes.number,
      brokenGoals: PropTypes.number,
      brokenCalls: PropTypes.number,
      oneOffs: PropTypes.number,
    }),
    goals: PropTypes.array,
    oneOffs: PropTypes.array,
  }).isRequired,
  isLoading: PropTypes.bool,
  isRequesting: PropTypes.bool,
  onRetry: PropTypes.func,
  onViewCalls: PropTypes.func,
};
