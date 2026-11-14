import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { useFixScenarioRows } from "src/api/simulate-environments/fixRuns";

import {
  checksLine,
  headline,
  pct,
  verdictCards,
  winnerOf,
} from "./fixRunView";
import { heldBackTally } from "./scenarioOutcomes";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";

const num = { fontVariantNumeric: "tabular-nums" };
const line = { typography: "s3", color: "text.subtitle" };

function Card({ card }) {
  const value =
    card.id === "gap" ? `${card.value} pts` : `${card.from}% → ${card.to}%`;
  return (
    <Box sx={{ px: 2, py: 0.875, minWidth: 0 }}>
      <Typography noWrap sx={line}>
        {card.label}
      </Typography>
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography noWrap sx={{ typography: "s1", fontWeight: 600, ...num }}>
          {value}
        </Typography>
        {!!card.delta && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              fontWeight: 600,
              ...num,
              color: card.delta > 0 ? BUILD_TONES.green : BUILD_TONES.red,
            }}
          >
            {`${card.delta > 0 ? "▲" : "▼"} ${Math.abs(card.delta)} pts`}
          </Typography>
        )}
      </Stack>
      <Typography noWrap sx={line}>
        {card.note}
      </Typography>
    </Box>
  );
}
Card.propTypes = { card: PropTypes.object.isRequired };

const tierIcon = (status) =>
  ({
    passed: "solar:check-circle-linear",
    failed: "solar:close-circle-bold",
    pending: "solar:clock-circle-linear",
    skipped: "solar:minus-circle-linear",
  })[status] || "solar:question-circle-linear";

function Checks({ tiers }) {
  const [all, setAll] = useState(false);
  const failed = tiers.filter((t) => t.status === "failed");
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 0.75, minWidth: 0 }}
      >
        <Iconify
          icon={
            failed.length
              ? "solar:close-circle-bold"
              : "solar:check-circle-linear"
          }
          width={14}
          sx={{
            color: failed.length ? BUILD_TONES.red : "text.secondary",
            flexShrink: 0,
          }}
        />
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}
        >
          {checksLine(tiers)}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: BUILD_TONES.red,
            flex: 1,
            minWidth: 0,
          }}
        >
          {failed.map((t) => t.name).join(" · ")}
        </Typography>
        <Button
          size="small"
          onClick={() => setAll((o) => !o)}
          endIcon={
            <Iconify
              icon={
                all
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={12}
            />
          }
          sx={{ color: "text.secondary", flexShrink: 0, minWidth: 0 }}
        >
          {all ? "Hide" : "Show all"}
        </Button>
      </Stack>
      {all && (
        <Box
          sx={{
            px: 2,
            pb: 1,
            pl: 4.25,
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "repeat(2, minmax(0, 1fr))" },
            columnGap: 3,
            rowGap: 0.5,
          }}
        >
          {tiers.map((t) => (
            <Stack
              key={t.name}
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ minWidth: 0 }}
            >
              <Iconify
                icon={tierIcon(t.status)}
                width={14}
                sx={{
                  color:
                    t.status === "failed" ? BUILD_TONES.red : "text.secondary",
                  flexShrink: 0,
                }}
              />
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                {t.name}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
              >
                {t.detail}
              </Typography>
            </Stack>
          ))}
        </Box>
      )}
    </Box>
  );
}
Checks.propTypes = { tiers: PropTypes.array.isRequired };

function MustPassBreaks({ verdict }) {
  const counted = verdict.must_pass_regressions || [];
  const noise = verdict.must_pass_within_noise || [];
  if (!counted.length && !noise.length) return null;
  return (
    <Box
      data-testid="must-pass-breaks"
      sx={{ px: 2, py: 0.75, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        Must-pass breaks
      </Typography>
      {counted.map((key) => (
        <Typography key={key} sx={{ typography: "s2", color: BUILD_TONES.red }}>
          {key}
        </Typography>
      ))}
      {noise.map((key) => (
        <Typography
          key={key}
          sx={{ typography: "s2", color: "text.secondary" }}
        >
          {`${key} · within normal noise, not counted`}
        </Typography>
      ))}
    </Box>
  );
}
MustPassBreaks.propTypes = { verdict: PropTypes.object.isRequired };

// Platform evals the winner scores worse on than the base version. They are
// shown, never counted: the verdict stands on the scenarios' own checks.
function EvalWarnings({ warnings }) {
  const evals = warnings.length === 1 ? "eval" : "evals";
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider", px: 2, py: 1 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Iconify
          icon="solar:danger-triangle-linear"
          width={14}
          sx={{ color: BUILD_TONES.amber, flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`Does worse on ${warnings.length} platform ${evals} than the current version. The verdict does not count them.`}
        </Typography>
      </Stack>
      <Box
        sx={{
          pl: 2.75,
          pt: 0.5,
          display: "grid",
          gridTemplateColumns: "minmax(0, max-content) 1fr",
          columnGap: 2,
          rowGap: 0.25,
        }}
      >
        {warnings.map((w, i) => [
          <Typography key={`${i}-name`} noWrap sx={{ typography: "s2" }}>
            {w.name}
          </Typography>,
          <Typography
            key={`${i}-rate`}
            sx={{
              typography: "s2",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {`${pct(w.pass_rate)}% vs ${pct(w.base_pass_rate)}% · ${w.trials} trials vs ${w.base_trials}`}
          </Typography>,
        ])}
      </Box>
    </Box>
  );
}
EvalWarnings.propTypes = { warnings: PropTypes.array.isRequired };

// The held-back cases a fix needs to be called verified. The read does not
// carry it: mirrors HELD_BACK_VERIFIED_MIN in simulate/pydantic_schemas/agent_fix.py.
const HELD_BACK_VERIFIED_MIN = 4;

// How many held-back scenarios the winner fixed, scenario by scenario as on
// the Per scenario tab, for a fix held back on too few cases to call it proven.
function HeldBackTally({ fix, winner }) {
  const { before, after, isLoading, isError } = useFixScenarioRows(
    fix,
    winner.id,
  );
  // A run whose calls did not load would skew the count, so say nothing.
  if (isLoading || isError) return null;
  const { fixed, failing } = heldBackTally(
    before,
    after,
    fix.request?.held_back_scenario_keys || [],
  );
  if (!failing) return null;
  const scenarios = failing === 1 ? "scenario" : "scenarios";
  return (
    <Typography
      sx={{
        px: 2,
        mt: -0.75,
        pb: 1,
        typography: "s2",
        color: "text.secondary",
      }}
    >
      {`Too few to tell · Fixed ${fixed} of ${failing} failing ${scenarios} it never saw — it takes ${HELD_BACK_VERIFIED_MIN} or more to tell.`}
    </Typography>
  );
}
HeldBackTally.propTypes = {
  fix: PropTypes.object.isRequired,
  winner: PropTypes.object.isRequired,
};

// The design's verdict block: the headline, the cards side by side, the checks
// on one line, then any must-pass breaks.
export default function FixVerdict({ fix }) {
  const winner = winnerOf(fix);
  const text = headline(fix);
  if (!winner || !text) return null;
  const cards = verdictCards(fix);
  // Too few held back to call it verified, whatever the verdict says.
  const limited = !!fix.limited_validation;
  const provenOnTargets = !limited && !winner.verdict.held_back_pass_rate;
  const heldBack = (fix.request?.held_back_scenario_keys || []).length > 0;
  return (
    <Box
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 1 }}
      >
        {limited && (
          <Box
            component="span"
            sx={{
              flexShrink: 0,
              px: 0.75,
              py: 0.25,
              borderRadius: 0.5,
              typography: "s3",
              fontWeight: 600,
              color: BUILD_TONES.amber,
              bgcolor: alpha(BUILD_TONES.amber, 0.12),
            }}
          >
            Limited validation
          </Box>
        )}
        <Typography
          sx={{ typography: "s1", fontWeight: 600, lineHeight: 1.45 }}
        >
          {text}
        </Typography>
      </Stack>
      {limited && heldBack && <HeldBackTally fix={fix} winner={winner} />}
      {provenOnTargets && (
        <Typography
          sx={{
            px: 2,
            mt: -0.75,
            pb: 1,
            typography: "s2",
            color: "text.secondary",
          }}
        >
          Proven on the targets only
        </Typography>
      )}
      <Box
        data-testid="verdict-cards"
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: `repeat(${cards.length}, minmax(0, 1fr))`,
          },
          borderTop: "1px solid",
          borderColor: "divider",
          "& > *": {
            borderColor: "divider",
            borderStyle: "solid",
            borderWidth: 0,
          },
          "& > *:not(:first-of-type)": { borderLeftWidth: 1 },
        }}
      >
        {cards.map((card) => (
          <Card key={card.id} card={card} />
        ))}
      </Box>
      {winner.verdict.tiers?.length > 0 && (
        <Checks tiers={winner.verdict.tiers} />
      )}
      <MustPassBreaks verdict={winner.verdict} />
      {fix.eval_warnings?.length > 0 && (
        <EvalWarnings warnings={fix.eval_warnings} />
      )}
    </Box>
  );
}

FixVerdict.propTypes = { fix: PropTypes.object.isRequired };
