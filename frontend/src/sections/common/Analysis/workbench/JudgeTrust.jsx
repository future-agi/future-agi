import React, { useState } from "react";
import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import { Link as RouterLink } from "react-router-dom";
import { Box, Link, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDatasetAnalysisSummary } from "src/api/develop/analysis";
import { useMatrix } from "src/api/develop/r2";
import StartReviewDialog from "src/sections/develop-detail/Review/StartReviewDialog";

const isJudgeKey = (key) => String(key ?? "").startsWith("eval:");

/**
 * Judge trust (R2-SPEC §6.3.5, F5 reference): how often a judge eval agrees
 * with reviewers, from the dataset's annotation summary `judge_agreement`
 * (the JudgeHumanMatrix numbers). `null` for Checks and non-eval measures;
 * `{checked:false}` when no review covers the eval yet.
 * @param {string} datasetId
 * @param {{key: string, evalType?: string}|string} measureOrKey
 */
export function useJudgeTrust(datasetId, measureOrKey) {
  const key =
    typeof measureOrKey === "string" ? measureOrKey : measureOrKey?.key;
  const judge =
    isJudgeKey(key) &&
    (typeof measureOrKey === "string" || measureOrKey?.evalType !== "Check");
  const { data, isLoading } = useDatasetAnalysisSummary(
    judge ? datasetId : null,
  );
  if (!judge || isLoading) return null;
  const item = (data?.judge_agreement ?? []).find((j) => j.metric_key === key);
  if (!item?.n) return { checked: false, key };
  return {
    checked: true,
    key,
    pct: item.agreement_pct,
    n: item.n,
    kappa: item.kappa,
    label: item.label_name,
  };
}

/** `Agrees with reviewers 84% (n=96)` or null. */
export const trustText = (trust) =>
  trust?.checked
    ? `Agrees with reviewers ${Math.round(trust.pct * 100)}% (n=${trust.n})`
    : null;

/**
 * One line: `Agrees with reviewers 84% (n=96) · Human review ↗`, or
 * `Not checked against reviewers yet · Start a review` (opens the existing
 * StartReviewDialog with 20 rows).
 */
export function TrustLine({
  trust,
  datasetId,
  runId,
  rowIds,
  typography = "s3",
}) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const { data: m } = useMatrix(
    { datasetId, runIds: runId ? [runId] : [] },
    { enabled: open && !rowIds?.length && Boolean(runId) },
  );
  const ids = rowIds?.length ? rowIds : (m?.rows ?? []).map((r) => r.row_id);
  if (!trust) return null;
  if (trust.checked)
    return (
      <Typography
        typography={typography}
        color="text.secondary"
        component="div"
      >
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={`Judge vs reviewers (${trust.label ?? "review label"}) on ${trust.n} rows${Number.isFinite(trust.kappa) ? ` · κ ${trust.kappa.toFixed(2)}` : ""}`}
        >
          <Box component="span" sx={{ color: "text.primary" }}>
            {trustText(trust)}
          </Box>
        </CustomTooltip>
        {" · "}
        <Link
          component={RouterLink}
          to={`/dashboard/develop/${datasetId}?tab=summary`}
          underline="hover"
        >
          Human review{" "}
          <Iconify
            icon="mdi:arrow-top-right"
            width={12}
            sx={{ verticalAlign: "middle" }}
          />
        </Link>
      </Typography>
    );
  return (
    <Typography typography={typography} color="text.secondary" component="div">
      Not checked against reviewers yet ·{" "}
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={() => setOpen(true)}
        sx={{ verticalAlign: "baseline", typography }}
      >
        Start a review
      </Link>
      {open && (
        <StartReviewDialog
          open
          onClose={() => setOpen(false)}
          datasetId={datasetId}
          mode="rubric"
          runA={runId}
          exampleIds={ids.slice(0, 20)}
          returnTo={`${location.pathname}${location.search}`}
        />
      )}
    </Typography>
  );
}

TrustLine.propTypes = {
  trust: PropTypes.object,
  datasetId: PropTypes.string,
  runId: PropTypes.string,
  rowIds: PropTypes.array,
  typography: PropTypes.string,
};
