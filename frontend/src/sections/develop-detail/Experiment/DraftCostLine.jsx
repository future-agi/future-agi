// IT-R5 G1 P4: the source footer's cost line (draftCostModel) with its `?`
// assumption and, for a code run with no model, `Add model`.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { IconButton, Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRunConfigs } from "src/api/develop/r2";
import { useExperimentForm } from "./ExperimentFormContext";
import { historyFromRuns } from "./costEstimateModel";
import { draftCostLine } from "./draftCostModel";

export default function DraftCostLine({
  draft,
  rows,
  tries,
  rowNoun,
  onAddModel,
}) {
  const { datasetId, judgeCount } = useExperimentForm();
  const r1 = useRunConfigs(datasetId);
  const history = useMemo(() => historyFromRuns(r1.data?.results), [r1.data]);
  // The judges the Evaluations step holds (the same count Review prices).
  const evals = useMemo(
    () =>
      Array.from({ length: Number(judgeCount) || 0 }, () => ({ type: "llm" })),
    [judgeCount],
  );
  const line = draftCostLine({ draft, rows, tries, history, rowNoun, evals });
  if (!line) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      columnGap={0.5}
      flexWrap="wrap"
      data-testid="source-cost-line"
      sx={{ minWidth: 0 }}
    >
      <Typography
        typography="s2"
        color="text.primary"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {line.text}
      </Typography>
      {line.needsModel && onAddModel ? (
        <Link
          component="button"
          type="button"
          typography="s2"
          onClick={onAddModel}
          data-testid="source-cost-add-model"
        >
          Add model
        </Link>
      ) : null}
      <CustomTooltip
        show
        title={line.assumption}
        size="small"
        placement="top"
        arrow
      >
        <IconButton
          size="small"
          aria-label="How this is estimated"
          data-testid="source-cost-help"
          sx={{ width: 24, height: 24, color: "text.secondary" }}
        >
          <Iconify icon="mdi:help-circle-outline" width={14} />
        </IconButton>
      </CustomTooltip>
    </Stack>
  );
}

DraftCostLine.propTypes = {
  draft: PropTypes.object,
  rows: PropTypes.number,
  tries: PropTypes.number,
  rowNoun: PropTypes.string,
  onAddModel: PropTypes.func,
};
