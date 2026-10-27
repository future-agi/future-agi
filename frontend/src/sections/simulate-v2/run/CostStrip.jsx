import { useMemo } from "react";
import PropTypes from "prop-types";
import { Stack, Tooltip, Typography } from "@mui/material";
import { StatusDot } from "../components/primitives";
import { resolveEval } from "../_mock/evals";
import { judgeCreditsOf, judgesOf, usageOfTask } from "../_mock/pricing";

/*
  What one scenario's calls cost, above whichever drawer opens it from the
  run's Cost tab — the voice call drawer or the trace. The same numbers as
  its Cost-tab row; each attempt's outcome, the one with the full trace in
  bold (the attempt that decided the verdict).
*/
const fmtN = (n) => Math.round(n).toLocaleString();

export default function CostStrip({ task, env, envState }) {
  const usage = useMemo(() => {
    const evals = (envState?.evals || []).map(resolveEval).filter(Boolean);
    return usageOfTask(task, {
      voice: ["voice", "multi"].includes(env?.surface),
      judgeCredits: judgeCreditsOf(evals),
      judges: judgesOf(evals),
      repeats: task?.samples?.length || 1,
    });
  }, [task, env, envState]);
  const callWord = ["voice", "multi"].includes(env?.surface)
    ? "call"
    : "conversation";
  const outcomes = (task?.samples || []).map((v) =>
    v === "passed" ? "passed" : "failed",
  );
  const traceFailed =
    task?.status !== "passed" && task?.status !== "unmeasured";
  const shownAt = Math.max(
    0,
    outcomes.indexOf(traceFailed ? "failed" : "passed"),
  );

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexWrap: "wrap",
        rowGap: 0.5,
        flexShrink: 0,
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {usage.charged ? `${fmtN(usage.credits)} credits` : "Not charged"}
      </Typography>
      <Typography
        sx={{
          typography: "s3",
          color: "text.secondary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {usage.charged
          ? `${usage.samples} ${callWord}${usage.samples === 1 ? "" : "s"} · simulation ${fmtN(usage.simCredits)} · evals ${fmtN(usage.evalCredits)}`
          : usage.why}
      </Typography>
      {outcomes.length > 1 && (
        <Stack direction="row" alignItems="center" gap={1} sx={{ ml: "auto" }}>
          {outcomes.map((o, i) => (
            <Tooltip
              // eslint-disable-next-line react/no-array-index-key
              key={i}
              arrow
              title={
                i === shownAt
                  ? `Attempt ${i + 1} ${o} · the one shown here`
                  : `Attempt ${i + 1} ${o} · kept as an outcome, not a full record`
              }
            >
              <Stack direction="row" alignItems="center" gap={0.5}>
                <StatusDot status={o} />
                <Typography
                  sx={{
                    typography: "s3",
                    color: i === shownAt ? "text.primary" : "text.tertiary",
                    fontWeight: i === shownAt ? 500 : 400,
                  }}
                >
                  {`Attempt ${i + 1}`}
                </Typography>
              </Stack>
            </Tooltip>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

CostStrip.propTypes = {
  task: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
};
