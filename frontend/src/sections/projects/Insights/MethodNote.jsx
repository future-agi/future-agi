import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Collapse, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { creditCount } from "src/sections/projects/LLMTracing/InstantEval/page/askFilter";
import {
  formatCost,
  formatCount,
  formatDuration,
  formatWhen,
  humanizeKey,
} from "./insightsFormat";
import { lensFor } from "./insightsLenses";
import { engineLabel, samplingText } from "./insightsReport";
import { shownScopeLabel } from "./insightsScope";

const STATUS_LABEL = {
  completed: "Completed",
  failed: "Failed",
  running: "Running",
  pending: "Queued",
};

// What the report itself cost, as the run recorded it when it was charged:
// its credits on Cloud, else its model cost. A ceiling price (a model with no
// listed price) is never shown as what something cost.
function costRow(cost) {
  if (cost?.credits > 0)
    return [
      "Charged",
      cost.unpriced
        ? `${creditCount(cost.credits)}, including calls to a model with no listed price, charged at the highest rate`
        : creditCount(cost.credits),
    ];
  if (cost?.usd > 0)
    return [
      "Model cost",
      cost.unpriced
        ? "Not known: a model it used has no listed price"
        : `${formatCost(cost.usd)} at list prices`,
    ];
  return ["Cost", null];
}

function rowsFor(run, config) {
  const settings = run.settings || {};
  const result = run.result || {};
  const models = run.models_used || {};
  const lens = settings.lens || config?.lens;
  const analysed = result.totals?.count ?? run.analysed_count;
  const excluded = Number(result.excluded) || 0;
  const failed = Number(result.failed) || 0;
  const skipped = Number(result.skipped) || 0;
  const start = run.started_at || run.created_at;
  return [
    ["Status", STATUS_LABEL[run.status] || run.status],
    [
      "Ran",
      [
        formatWhen(start),
        run.finished_at && `to ${formatWhen(run.finished_at)}`,
        run.finished_at && `(${formatDuration(start, run.finished_at)})`,
      ]
        .filter(Boolean)
        .join(" "),
    ],
    ["Scope", shownScopeLabel(run.scope)],
    ["Sampled", samplingText(run)],
    ["Analysed", Number.isFinite(analysed) ? formatCount(analysed) : null],
    [
      "Left out",
      excluded > 0
        ? `${formatCount(excluded)} (${lens === "failures" ? "no agent failure found" : "not relevant to the question"})`
        : null,
    ],
    [
      "Could not be read",
      failed + skipped > 0
        ? [
            failed > 0 && `${formatCount(failed)} failed`,
            skipped > 0 && `${formatCount(skipped)} had nothing to read`,
          ]
            .filter(Boolean)
            .join(", ")
        : null,
    ],
    [
      "Categories",
      run.taxonomy_mode === "classify"
        ? "Sorted into the report's saved categories"
        : `Discovered in this run${result.clustering?.k ? ` from ${result.clustering.k} groups` : ""}`,
    ],
    ["Lens", lensFor(lens).title],
    ["Question", settings.question || config?.question],
    ["What the agent does", settings.agent_description],
    ["Instructions", settings.instructions],
    [
      "Attributes",
      (settings.attributes || [])
        .map(
          (a) =>
            `${humanizeKey(a.key)} (${a.type}${a.filter_by ? ", filters" : ""})`,
        )
        .join(", "),
    ],
    ["Engine", engineLabel(result.engine)],
    [
      "Models",
      [
        models.fast && `read by ${models.fast}`,
        models.thinking && `grouped and written by ${models.thinking}`,
      ]
        .filter(Boolean)
        .join(", "),
    ],
    costRow(result.cost),
  ].filter(([, value]) => value);
}

/** "How this report was made": the run's settings, sample and models. */
export default function MethodNote({ run, config, open, onToggle }) {
  const rows = rowsFor(run, config);
  return (
    <Box
      id="insights-method"
      component="section"
      sx={{ borderTop: "1px solid", borderColor: "divider", pt: 1 }}
    >
      <Button
        color="inherit"
        onClick={onToggle}
        aria-expanded={open}
        startIcon={
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={18}
          />
        }
        sx={{ px: 0.75, ml: -0.75, fontSize: 14, fontWeight: 600 }}
      >
        How this report was made
      </Button>
      <Collapse in={open}>
        <Box
          component="dl"
          sx={{
            m: 0,
            pt: 1,
            pb: 1,
            display: "grid",
            gridTemplateColumns: "160px minmax(0, 1fr)",
            rowGap: 0.75,
            columnGap: 2,
          }}
        >
          {rows.map(([label, value]) => (
            <React.Fragment key={label}>
              <Typography
                component="dt"
                variant="body2"
                color="text.secondary"
                sx={{ fontSize: 13 }}
              >
                {label}
              </Typography>
              <Typography
                component="dd"
                variant="body2"
                sx={{ m: 0, fontSize: 13, overflowWrap: "anywhere" }}
              >
                {value}
              </Typography>
            </React.Fragment>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

MethodNote.propTypes = {
  run: PropTypes.object.isRequired,
  config: PropTypes.object,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
};
