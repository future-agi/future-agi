import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import { paths } from "src/routes/paths";
import { improvementOf } from "../_mock/runIdentity";
import { improvementRunName, rejectedFor } from "../_mock/optimizer";
import {
  chosenTrialOf,
  chosenVerified,
  projectedHeld,
} from "../_mock/optimizationRuns";

const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)}`;

/**
 * On a run an improvement made — one of its trials, or its winner on the
 * full suite — what this run is to the improvement, above the run's own tabs.
 *
 *   A trial that wasn't kept says so first, so its page is never read as the
 *   fix: rejected (and why), or not kept because another trial won or the
 *   search found nothing safe.
 *   Every one says what it did against the run the improvement started from,
 *   with Compare beside it — an improvement's answer is a before and after.
 */
export default function ImprovementRunStrip({
  env,
  envState,
  runId,
  onOpenSearch,
}) {
  const navigate = useNavigate();
  const of = improvementOf({ id: runId }, envState);
  if (!of?.record?.result) return null;
  const { record, role } = of;
  const result = record.result;
  const name = improvementRunName(envState, record);
  const fromRun =
    (envState?.runs || []).find((r) => r.id === record.fromRunId) || null;
  const winner = result.winner || null;
  const keptN = record.preferredTrial || winner?.n || null;
  const n = role === "trial" ? Number(/-t(\d+)$/.exec(runId)?.[1]) : keptN;
  const trial = (result.trials || []).find((t) => t.n === n) || null;

  /* Why this trial's change didn't go anywhere — null when it was kept. */
  const reason =
    role !== "trial" || n === keptN
      ? null
      : rejectedFor(trial)
        ? `Rejected — ${rejectedFor(trial)}.`
        : keptN
          ? `Not kept — Run ${keptN} won.`
          : "Not kept — the search found no safe improvement.";
  const shippedNothing = !keptN;

  /*
    Against the run it started from. The kept run's held-back figure is its
    own (the search's pick, a finalist) or projected until it's verified;
    every fixed and broke count says which set it's from — the target
    scenarios each run was scored on.
  */
  const vs = fromRun
    ? `Run ${fromRun.ordinal || ""}`.trim()
    : "the agent as it was";
  const chosen = chosenTrialOf(record);
  const kept = chosen || winner;
  const keptHeld = chosen ? projectedHeld(result, chosen) : result.heldScore;
  const projected =
    !!chosen &&
    !chosenVerified(record) &&
    !(result.finalists || []).some((f) => f.n === chosen.n);
  const parts =
    role === "winner"
      ? [
          result.heldBase != null && keptHeld != null
            ? `${projected ? "projected " : ""}${signed(keptHeld - result.heldBase)} pts held back`
            : null,
          kept ? `target: fixed ${kept.fixed || 0}` : null,
          kept ? `broke ${kept.broke || 0}` : null,
        ]
      : [
          trial ? `${signed(trial.delta)} pts on the target scenarios` : null,
          trial ? `target: fixed ${trial.fixed || 0}` : null,
          trial ? `broke ${trial.broke || 0}` : null,
        ];
  const broke = role === "winner" ? kept?.broke : trial?.broke;
  const compareHref = fromRun
    ? `${paths.dashboard.simulate.simulationCompare(env.id)}?runs=${fromRun.id},${runId}`
    : null;

  const link = (label, onClick) => (
    <Box
      component="span"
      {...clickable(onClick, { role: "link" })}
      sx={{
        color: "text.primary",
        fontWeight: 600,
        cursor: "pointer",
        whiteSpace: "nowrap",
        "&:hover": { textDecoration: "underline" },
        ...focusRing,
      }}
    >
      {label}
    </Box>
  );

  return (
    <Box sx={{ px: 2, pt: 1 }}>
      {reason && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 1.75,
            py: 1,
            mb: 1,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "status.danger.border",
            bgcolor: "status.danger.bg",
          }}
        >
          <Iconify
            icon="solar:close-circle-linear"
            width={16}
            sx={{ color: "status.danger.fg", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
            <Box component="span" sx={{ fontWeight: 600 }}>
              {reason}
            </Box>
            <Box component="span" sx={{ color: "text.secondary" }}>
              {shippedNothing
                ? ` Nothing from ${name} was shipped.`
                : ` This change wasn't shipped.`}
            </Box>
          </Typography>
          {onOpenSearch && (
            <Typography sx={{ typography: "s2", flexShrink: 0 }}>
              {link("See the search", onOpenSearch)}
            </Typography>
          )}
        </Stack>
      )}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 0.25 }}>
        <Iconify
          icon="solar:transfer-horizontal-linear"
          width={14}
          sx={{ color: "text.tertiary", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: "text.secondary",
            minWidth: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${role === "winner" ? `${name}'s winner on the full suite` : `Run ${n} of ${name}`} vs ${vs}: `}
          {parts.filter(Boolean).map((p, i) => (
            <Box
              key={p}
              component="span"
              sx={{
                color:
                  p.startsWith("broke") && broke
                    ? "status.danger.fg"
                    : "text.primary",
                fontWeight: 600,
              }}
            >
              {`${i ? " · " : ""}${p}`}
            </Box>
          ))}
        </Typography>
        {compareHref && (
          <Typography sx={{ typography: "s2", flexShrink: 0 }}>
            {" · "}
            {link("Compare", () => navigate(compareHref))}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}

ImprovementRunStrip.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  runId: PropTypes.string,
  onOpenSearch: PropTypes.func,
};
