import React, { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import ModelSelector from "src/sections/evals/components/ModelSelector";
import { DecisionJudgesContext } from "src/sections/common/DecisionJudges/DecisionJudgesContext";
import {
  compareConfirm,
  headlineOf,
  kappaText,
  openRowsHref,
  pctText,
} from "./agreementModel";
import { useAgreement, useCustomPrices } from "./alignmentApi";
import { usdText } from "./judgeCost";

const tabular = { fontVariantNumeric: "tabular-nums" };

function JudgeColumn({ title, stats }) {
  return (
    <Box
      sx={{
        flex: 1,
        minWidth: 240,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Typography typography="s2" fontWeight={600}>
        {title}
      </Typography>
      <Typography typography="s2" sx={tabular}>
        {headlineOf(stats)}
      </Typography>
      <Typography typography="s3" color="text.secondary" sx={tabular}>
        {(stats.labels ?? [])
          .map(
            (hl, h) =>
              `Reviewers ${hl}: ${(stats.labels ?? [])
                .map((jl, j) => `judge ${jl} ${stats.matrix?.[h]?.[j] ?? 0}`)
                .join(", ")}`,
          )
          .join(" · ")}
      </Typography>
    </Box>
  );
}

JudgeColumn.propTypes = { title: PropTypes.string, stats: PropTypes.object };

/**
 * Compare with another judge: both judges score the labelled rows through
 * the one scorer (nothing is stored); each against the reviewers, and the
 * two against each other. Its own query: the report and the panels around
 * it stay as they are. The confirm and the billed line price the same
 * calls (the one estimator; the run ledger's rule).
 */
const ComparePanel = ({ templateId, report }) => {
  const navigate = useNavigate();
  const [model, setModel] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [compareModel, setCompareModel] = useState(null);
  const { customPrices, refresh: refreshPrices } = useCustomPrices();
  const compared = useAgreement(
    templateId,
    { source: report.source_key, compare_judge_model: compareModel },
    { enabled: Boolean(compareModel), keepPrevious: true },
  );
  const cmp = compareModel ? compared.data?.report?.compare ?? null : null;
  const loading = compared.isFetching;
  const n = (report.labelled_rows ?? []).length;
  const est = model
    ? compareConfirm(n, report.judge_model, model, customPrices)
    : null;
  const differ = cmp?.judge_vs_judge?.differ_rows ?? [];
  const differHref = openRowsHref(
    report.source,
    differ,
    `${cmp?.current?.judge_model} ≠ ${cmp?.judge_model}`,
  );
  return (
    <Stack gap={1.5} data-testid="w7-compare">
      <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
        <Typography typography="s1" fontWeight={600}>
          Compare with another judge
        </Typography>
        <Box sx={{ minWidth: 220 }}>
          <DecisionJudgesContext.Provider value>
            <ModelSelector
              model={model}
              onModelChange={(m) =>
                setModel(typeof m === "string" ? m : m?.value ?? "")
              }
              showMode={false}
              showPlus={false}
              hideDatasetContextToggle
            />
          </DecisionJudgesContext.Provider>
        </Box>
        <Button
          size="small"
          variant="contained"
          disabled={!model || !n || loading}
          onClick={async () => {
            await refreshPrices();
            setConfirm(true);
          }}
          data-testid="w7-compare-run"
        >
          Compare
        </Button>
      </Stack>
      {cmp ? (
        <Stack gap={1}>
          <Stack direction={{ xs: "column", md: "row" }} gap={1.5}>
            <JudgeColumn
              title={`${cmp.current.judge_model ?? "Current judge"} (current) vs reviewers`}
              stats={cmp.current}
            />
            <JudgeColumn
              title={`${cmp.judge_model} vs reviewers`}
              stats={cmp}
            />
          </Stack>
          <Stack direction="row" gap={1.5} alignItems="center">
            <Typography typography="s2" sx={tabular} data-testid="w7-jvj">
              Judge vs judge: {pctText(cmp.judge_vs_judge.agreement)} agree · κ{" "}
              {kappaText(cmp.judge_vs_judge.kappa)} · {differ.length} rows
              differ
            </Typography>
            {differHref ? (
              <Button size="small" onClick={() => navigate(differHref)}>
                Open rows
              </Button>
            ) : null}
          </Stack>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={tabular}
            data-testid="w7-compare-billed"
          >
            Both judges scored the same {n} labelled rows ({cmp.judge_calls}{" "}
            judge calls) with today&apos;s definition, billed as a run bills
            them: {cmp.judge_model} {usdText(cmp.scored_usd?.other)},{" "}
            {cmp.current?.judge_model ?? "current judge"}{" "}
            {usdText(cmp.scored_usd?.current)} (
            {usdText(
              (cmp.scored_usd?.other ?? 0) + (cmp.scored_usd?.current ?? 0),
            )}{" "}
            in all). Nothing was saved on a run.
          </Typography>
        </Stack>
      ) : null}
      <Dialog open={confirm} onClose={() => setConfirm(false)}>
        <DialogTitle>Compare judges</DialogTitle>
        <DialogContent>
          <Typography typography="s1" data-testid="w7-compare-confirm">
            {est?.text}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => {
              setConfirm(false);
              setCompareModel(model);
            }}
            data-testid="w7-compare-confirm-go"
          >
            Score
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

ComparePanel.propTypes = {
  templateId: PropTypes.string.isRequired,
  report: PropTypes.object.isRequired,
};

export default ComparePanel;
