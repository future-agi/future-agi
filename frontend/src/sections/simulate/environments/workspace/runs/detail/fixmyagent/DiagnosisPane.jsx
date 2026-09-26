import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha, keyframes } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  CircularProgress,
  Button,
  Link,
} from "@mui/material";

import Iconify from "src/components/iconify";
import EmptyState from "../../../../components/EmptyState";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import GoalCard, { OneOffRow } from "./GoalCard";

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

function summaryOf({ summary }) {
  if (!summary) return "No results for this run yet.";
  const { brokenGoals, brokenCalls, measuredCalls, oneOffs } = summary;
  const also = oneOffs ? `, plus ${plural(oneOffs, "one-off issue")}` : "";
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

// Calls that errored never count against the agent; each line links to its
// calls so the exclusion can be checked.
function coverageOf({ summary }) {
  const excluded = summary?.excludedCallIds ?? [];
  const unread = summary?.unanalyzedCallIds ?? [];
  return [
    excluded.length > 0 && {
      text: `${plural(excluded.length, "call")} didn't complete, so ${
        excluded.length === 1 ? "it isn't" : "they aren't"
      } counted.`,
      callIds: excluded,
    },
    unread.length > 0 && {
      text: `${plural(unread.length, "call")} ${
        unread.length === 1 ? "hasn't" : "haven't"
      } been analysed yet, so some issues may be missing.`,
      callIds: unread,
      // Most of these are a model hiccup; one retry reads them again.
      retryable: true,
    },
  ].filter(Boolean);
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

  return (
    <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
      <Box
        sx={{
          mx: 2.5,
          mt: 2.5,
          px: 1.75,
          py: 1.5,
          borderRadius: 1,
          border: "1px solid",
          borderColor: (t) =>
            alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.35 : 0.25),
          bgcolor: (t) =>
            alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.1 : 0.05),
        }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1.25}>
          <Iconify
            icon="solar:lightbulb-bolt-linear"
            width={15}
            sx={{ color: BUILD_TONES.accent, flexShrink: 0, mt: "2px" }}
          />
          <Box>
            <Typography sx={{ typography: "s2" }}>
              {summaryOf(analysis)}
            </Typography>
            {coverageOf(analysis).map(({ text, callIds, retryable }) => (
              <Typography
                key={text}
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
              >
                {text}{" "}
                <Link
                  component="button"
                  onClick={() => onViewCalls?.(callIds)}
                  sx={{ typography: "s3", verticalAlign: "baseline" }}
                >
                  View
                </Link>
                {retryable && onRetry && (
                  <>
                    {" · "}
                    <Link
                      component="button"
                      onClick={onRetry}
                      sx={{ typography: "s3", verticalAlign: "baseline" }}
                    >
                      Try again
                    </Link>
                  </>
                )}
              </Typography>
            ))}
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

      {analysis.goals.length > 0 && (
        <Stack spacing={1.25} sx={{ px: 2.5, pt: 2.5 }}>
          {analysis.goals.map((goal) => (
            <GoalCard key={goal.goal} goal={goal} onViewCalls={onViewCalls} />
          ))}
        </Stack>
      )}

      {analysis.oneOffs.length > 0 && (
        <Box sx={{ px: 2.5, pt: 2.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.5 }}>
            Also seen, 1 call each
          </Typography>
          {analysis.oneOffs.map((way) => (
            <OneOffRow key={way.id} way={way} onViewCalls={onViewCalls} />
          ))}
        </Box>
      )}

      <Box sx={{ height: 20 }} />
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
      excludedCallIds: PropTypes.arrayOf(PropTypes.string),
      unanalyzedCallIds: PropTypes.arrayOf(PropTypes.string),
    }),
    goals: PropTypes.array,
    oneOffs: PropTypes.array,
  }).isRequired,
  isLoading: PropTypes.bool,
  isRequesting: PropTypes.bool,
  onRetry: PropTypes.func,
  onViewCalls: PropTypes.func,
};
