import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { creditsLabel } from "../_mock/metering";
import {
  OPT_STATUS,
  reproduceMsOf,
  searchSpend,
} from "../_mock/optimizationRuns";
import { envAgentKey } from "../_mock/agentRegistry";
import { limitsOf } from "../agents/agentLimits";
import { useConnectedAgents } from "../store";
import { ConstraintPicker } from "./ImprovementConstraints";
import { BUDGET_STEPS, MINUTE_STEPS, minutesLabel } from "./constraintEdits";

/*
  An improvement that stopped at its budget or time cap, raised where it
  stopped: pick more, Continue, and the same improvement searches on —
  never a trip to the agent's page. Past the agent's own limit it applies to
  this improvement only, and the picker asks in place whether the agent's
  limit should move too.

  `plan` is the whole search it had planned: what it held back when it
  stopped before searching (pendingResult), or the rest of the search when
  it stopped partway (continueResult).
*/
export default function RaiseLimitBar({
  record,
  env,
  envState,
  patch,
  framed = true,
}) {
  const { agents, state } = useConnectedAgents();
  const key = envAgentKey(envState?.agent, env?.id);
  const agent = agents.find((a) => a.key === key) || null;
  const agentLimits = limitsOf(state, key);
  const s = record?.stoppedAtBudget || {};
  const before = !s.trials;
  const plan = before ? record?.pendingResult : record?.continueResult;
  const byTime = s.by === "time";
  const nowMinutes = record?.limits?.budget?.minutes || s.minutes || 0;
  const needed = plan
    ? Math.ceil(searchSpend(plan, env?.surface) + (record.spentFirst || 0))
    : null;
  /* More than it had, and (for credits) enough for the whole search. */
  const budgetSteps = BUDGET_STEPS.filter(
    (b) => b > (record?.budget || 0) && (needed == null || b >= needed),
  );
  const minuteSteps = MINUTE_STEPS.filter((m) => m > nowMinutes);
  const [dollars, setDollars] = useState(null);
  const [minutes, setMinutes] = useState(null);
  const pickDollars = byTime ? record?.budget : dollars ?? budgetSteps[0];
  const pickMinutes = byTime ? minutes ?? minuteSteps[0] : nowMinutes;
  const steps = byTime ? minuteSteps : budgetSteps;
  if (!record?.stoppedAtBudget || !plan || !patch) return null;

  const resume = () =>
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id
          ? {
              ...o,
              status: OPT_STATUS.RUNNING,
              budget: pickDollars,
              limits: {
                ...(o.limits || {}),
                budget: {
                  ...(o.limits?.budget || {}),
                  dollars: Math.max(
                    pickDollars,
                    o.limits?.budget?.dollars || 0,
                  ),
                  minutes: pickMinutes,
                },
              },
              /* What it had before the raise — said on its Cost tab. */
              raisedFrom: { budget: o.budget, minutes: nowMinutes },
              /* Reproduce already ran — the search picks up after it. */
              startedAt: new Date(Date.now() - reproduceMsOf(o)).toISOString(),
              result: plan,
              pendingResult: undefined,
              continueResult: undefined,
              pendingEnding: undefined,
              stoppedAtBudget: undefined,
              completedAt: undefined,
            }
          : o,
      ),
    });

  const said = byTime
    ? `Stopped at its ${minutesLabel(nowMinutes)} time cap${before ? " before searching" : ` after ${s.trials} of ${s.of} trials`}.`
    : `Stopped at its ${creditsLabel(record.budget || 0)} budget${before ? " before searching" : ` after ${s.trials} of ${s.of} trials`}.`;
  const ask = steps.length
    ? before
      ? `The whole search needs about ${creditsLabel(needed || 0)}. Raise it to continue?`
      : "The best it found is kept. Raise it to continue the search?"
    : byTime
      ? "It already had the longest time cap — improve towards a narrower goal instead."
      : `The whole search needs about ${creditsLabel(needed || 0)}, more than the largest budget — improve towards a narrower goal instead.`;

  /* What Continue keeps and what it starts over — before it acts. */
  const resumeNote = before
    ? "Continue runs the whole search. Reproduce already ran, so it isn't repeated."
    : `Continue keeps the ${s.trials} trial${s.trials === 1 ? "" : "s"} it finished — not repeated or charged again — and runs the other ${Math.max(0, (s.of || 0) - s.trials)}. A trial that was partway starts over from its seed.`;
  const controls = steps.length > 0 && (
    <Box>
      <Stack
        direction="row"
        spacing={1}
        alignItems="flex-start"
        sx={{ mt: 1.25 }}
      >
        <Box sx={{ width: 220 }}>
          <ConstraintPicker
            agent={agent}
            limits={agentLimits}
            field={byTime ? "minutes" : "dollars"}
            value={byTime ? pickMinutes : pickDollars}
            onChange={byTime ? setMinutes : setDollars}
            steps={steps}
            fullWidth
            sx={{ height: 32 }}
          />
        </Box>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={resume}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
          sx={{ height: 32, flexShrink: 0 }}
        >
          Continue
        </Button>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        {resumeNote}
      </Typography>
    </Box>
  );

  if (!framed)
    return (
      <Box>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {ask}
        </Typography>
        {controls}
      </Box>
    );
  return (
    <Box
      sx={{
        mb: 2,
        px: 2,
        py: 1.5,
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify
          icon="solar:pause-circle-linear"
          width={16}
          sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
        />
        <Box sx={{ minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {said}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {ask}
          </Typography>
          {controls}
        </Box>
      </Stack>
    </Box>
  );
}
RaiseLimitBar.propTypes = {
  record: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  framed: PropTypes.bool,
};
