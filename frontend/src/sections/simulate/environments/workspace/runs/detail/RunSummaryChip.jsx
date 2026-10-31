import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRunReliability } from "src/api/simulate-environments/runAnalytics";

import UiOnlyFlag from "../../../components/UiOnlyFlag";
import ReliabilityHeadline from "./ReliabilityHeadline";
import { countsOnly } from "./runHeaderFormat";

const chipSx = {
  px: 0.75,
  height: 22,
  borderRadius: "4px",
  flexShrink: 0,
  cursor: "default",
  color: "text.primary",
  border: "1px solid",
  borderColor: "divider",
};

// The chip's words: the pass rate, then how many scenarios passed every
// repeat. Under 10 scenarios a rate would overstate itself, so raw trials.
function chipLine(reliability) {
  if (countsOnly(reliability))
    return `${reliability.trials_passed}/${reliability.trials_evaluated} trials passed`;
  const passK =
    reliability.k > 1 && reliability.pass_hat_k != null
      ? ` · ${Math.round(reliability.pass_hat_k)}% all ${reliability.k} repeats`
      : "";
  return `${Math.round(reliability.pass_hat_1)}% passed${passK}`;
}

/**
 * The run summary, compact, beside the status chip: "66% passed · 51% all 3
 * repeats". The full reliability read (pass^1, pass^k, the 95% range and the
 * inconclusive trials) is its hover card. Finished runs only.
 */
export default function RunSummaryChip({ executionId, enabled }) {
  const { data: reliability } = useRunReliability(executionId, { enabled });
  if (!enabled || reliability?.pass_hat_1 == null) return null;
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      placement="bottom-start"
      title={<ReliabilityHeadline reliability={reliability} />}
    >
      <Stack
        data-testid="run-summary-chip"
        direction="row"
        alignItems="center"
        sx={chipSx}
      >
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "inherit",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {chipLine(reliability)}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}
RunSummaryChip.propTypes = {
  executionId: PropTypes.string,
  enabled: PropTypes.bool,
};

const PROOF_ICON = {
  live: "solar:global-linear",
  "prompt-only": "solar:document-text-linear",
};

/**
 * Sealed, Live or Prompt only: where the run's agent ran and how its tools
 * were handled, as a mark with its words on hover. Sample only
 * (?uiSample=proofKind): the API records no proof kind for a run yet.
 */
export function ProofKindChip({ proof }) {
  if (!proof?.label) return null;
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <CustomTooltip
        show
        arrow
        size="small"
        title={
          <Stack spacing={0.5}>
            <Box component="b">{`Proof · ${proof.label}${proof.tierLabel ? ` · ${proof.tierLabel}` : ""}`}</Box>
            <span>{proof.note}</span>
          </Stack>
        }
      >
        <Box
          component="span"
          aria-label={`Proof · ${proof.label}`}
          sx={{
            display: "inline-flex",
            color: "text.subtitle",
            cursor: "default",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon={PROOF_ICON[proof.kind] ?? "solar:box-linear"}
            width={16}
          />
        </Box>
      </CustomTooltip>
      <UiOnlyFlag />
    </Stack>
  );
}
ProofKindChip.propTypes = { proof: PropTypes.object };
