import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { RunTraceLog } from "../components/RunTrace";
import { goalWords, paretoPool, searchTrace } from "../_mock/optimizer";
import { methodLabelOf } from "../run/fixmyagent/improvementView";
import { FrozenInputs } from "../run/fixmyagent/ProofParts";
import { OtherEnvironments } from "../run/fixmyagent/ImprovementPanels";

/**
 * The bottom of an epoch's Candidates tab, collapsed: how it searched (the
 * method, the scenario sets, the full trace), what it was measured against
 * (the issue snapshot and frozen inputs) and how each finalist did in the
 * other environments it was checked in. Proof of work, kept out of the way.
 */
export default function EpochSearchDetails({
  record,
  model,
  identity,
  envSnapshot,
}) {
  const [open, setOpen] = useState(false);
  const result = record?.result;
  if (!result) return null;
  const pool = result.optimizer?.id === "gepa" ? paretoPool(result) : [];
  return (
    <Box
      sx={{
        mt: 2,
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
      }}
    >
      <ButtonBase
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        sx={{
          width: "100%",
          justifyContent: "flex-start",
          gap: 0.75,
          px: 2,
          minHeight: 36,
          borderRadius: "8px",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          Search details
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {`${methodLabelOf(result.optimizer?.id)} search · ${result.trials.length}${result.planned && result.planned !== result.trials.length ? ` of ${result.planned}` : ""} candidates · frozen inputs`}
        </Typography>
      </ButtonBase>
      {open && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider", pt: 1.5 }}>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", px: 2.5, pb: 1 }}
          >
            {`${methodLabelOf(result.optimizer?.id)} search (${result.optimizer?.label}) · judged by a different model from the one that wrote the changes, so no model grades its own work · ${result.trainMeasured} target / ${result.heldMeasured} held-back${result.mustPassMeasured != null ? ` / ${result.mustPassMeasured} must-pass` : ""} scenarios${result.goal ? ` · targets: scenarios short of ${goalWords(result.goal)}` : ""}`}
          </Typography>
          {/* What every verdict was computed against. */}
          <FrozenInputs
            record={record}
            model={model}
            identity={identity}
            envSnapshot={envSnapshot}
          />
          {/* Each finalist, once in every other environment picked. */}
          <OtherEnvironments result={result} />
          {/* GEPA keeps the best candidate for each scenario, not one overall best. */}
          {pool.length > 0 && (
            <Box sx={{ px: 2.5, pt: 1 }}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                }}
              >
                {`Kept · best per scenario · ${pool.length} ${pool.length === 1 ? "candidate" : "candidates"}`}
              </Typography>
              {pool.map((x) => (
                <Stack key={x.n} direction="row" spacing={1.5} sx={{ py: 0.5 }}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      width: 96,
                      flexShrink: 0,
                    }}
                  >{`Candidate ${x.n}`}</Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.secondary",
                      width: 44,
                      flexShrink: 0,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >{`${x.score}%`}</Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      color: "text.tertiary",
                      minWidth: 0,
                    }}
                  >
                    {`best on ${x.scenarios.length}: ${x.scenarios.slice(0, 3).join(", ")}${x.scenarios.length > 3 ? ` +${x.scenarios.length - 3}` : ""}`}
                  </Typography>
                </Stack>
              ))}
            </Box>
          )}
          <Box sx={{ px: 1.5, pb: 1 }}>
            <RunTraceLog
              label={`Searched by ${methodLabelOf(result.optimizer?.id).toLowerCase()} search (${result.optimizer?.label}) · ${result.trials.length} candidates`}
              steps={searchTrace(result)}
            />
          </Box>
        </Box>
      )}
    </Box>
  );
}

EpochSearchDetails.propTypes = {
  record: PropTypes.object,
  model: PropTypes.string,
  identity: PropTypes.string,
  envSnapshot: PropTypes.string,
};
