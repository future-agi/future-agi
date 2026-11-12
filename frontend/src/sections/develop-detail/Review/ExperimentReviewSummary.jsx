import React, { useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Collapse,
  Link,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import Term from "src/sections/common/ux/Term";
import CopyButton from "src/sections/common/ux/CopyButton";
import { fmtPct } from "src/sections/common/format/fmt";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAnnotationQueueDetail } from "src/api/annotation-queues/annotation-queues";
import {
  compareUrl,
  judgeAgreementLine,
  myJudgeDiffLine,
  winRateHeadline,
  winRateLine,
} from "./reviewModel";
// W4 C1 / C3: the review's corrections and its outside reviewers.
import CorrectionsBlock from "./CorrectionsBlock";
import OutsideReviewersSection from "./OutsideReviewersSection";

const pct = (x) => fmtPct(x, { dp: 0 });

// RW#26: colour-blind-safe win / tie / loss (blue · grey · orange), with
// the share printed on the bar, never colour alone.
const WIN = "info.main";
const TIE = "grey.400";
const LOSS = "orange.500";

function Bar({ parts }) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  return (
    <Box
      sx={{
        display: "flex",
        height: 18,
        borderRadius: 1,
        overflow: "hidden",
        bgcolor: "action.hover",
      }}
    >
      {parts
        .filter((p) => p.value > 0)
        .map((p) => (
          <CustomTooltip
            key={p.label}
            show
            size="small"
            placement="top"
            arrow
            title={p.tip ?? `${p.label}: ${p.value}`}
          >
            <Box
              sx={{
                width: `${(p.value / total) * 100}%`,
                bgcolor: p.color,
                "&:not(:last-of-type)": { mr: "2px" },
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "common.white",
                typography: "s3",
                fontWeight: 600,
                overflow: "hidden",
                whiteSpace: "nowrap",
              }}
            >
              {p.value / total >= 0.12 ? pct(p.value / total) : ""}
            </Box>
          </CustomTooltip>
        ))}
    </Box>
  );
}

Bar.propTypes = { parts: PropTypes.array.isRequired };

function Legend({ items }) {
  return (
    <Stack direction="row" gap={1.5} flexWrap="wrap" justifyContent="center">
      {items.map((it) => (
        <Stack key={it.label} direction="row" alignItems="center" gap={0.5}>
          <Box
            sx={{ width: 8, height: 8, borderRadius: "2px", bgcolor: it.color }}
          />
          <Typography typography="s2" color="text.secondary">
            {it.label}{" "}
            <Box
              component="span"
              sx={{ color: "text.primary", fontWeight: 500 }}
            >
              {it.value}
            </Box>
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

Legend.propTypes = { items: PropTypes.array.isRequired };

function PairwiseSummary({ s, datasetId, from }) {
  const [reveal, setReveal] = useState(false);
  const labelA = s.run_a?.label ?? "Run A";
  const labelB = s.run_b?.label ?? "Run B";
  const line = winRateLine(s, { labelB, rows: s.judged });
  if (!line)
    return (
      <Typography typography="s1" color="text.secondary">
        No votes yet on {labelB} vs {labelA}.
      </Typography>
    );
  const agreement = s.judge?.agreement;
  // integration (RV-09): the headline says what the number is (ties count half);
  // the agreement line and your picks come from one count each.
  const headline =
    winRateHeadline(s, { labelB, rows: s.judged }) ?? line.headline;
  const agreementText = judgeAgreementLine(agreement);
  const [lo, hi] = s.ci ?? [];
  const width = lo != null && hi != null ? hi - lo : null;
  const evidence =
    width == null
      ? null
      : width <= 0.2
        ? "Evidence is strong."
        : width <= 0.35
          ? `Evidence is moderate: about ${Math.max(1, Math.round(s.judged * 0.3))} more rows would tighten the range.`
          : `Evidence is weak: review about ${s.judged} more rows before deciding.`;
  const parts = [
    {
      label: `${labelB} better`,
      value: s.wins,
      color: WIN,
      tip: `${s.wins} ${s.wins === 1 ? "row" : "rows"} (${pct(s.wins / s.judged)}) where reviewers preferred ${labelB}`,
    },
    {
      label: "Tie",
      value: s.ties,
      color: TIE,
      tip: `${s.ties} ${s.ties === 1 ? "row" : "rows"} (${pct(s.ties / s.judged)}) judged a tie`,
    },
    {
      label: `${labelA} better`,
      value: s.losses,
      color: LOSS,
      tip: `${s.losses} ${s.losses === 1 ? "row" : "rows"} (${pct(s.losses / s.judged)}) where reviewers preferred ${labelA}`,
    },
  ];
  const summaryText = [
    headline,
    lo != null
      ? `Likely between ${pct(lo)} and ${pct(hi)} (95% confidence).`
      : null,
    `${s.wins} wins, ${s.ties} ties, ${s.losses} losses for ${labelB}.`,
    agreementText ? `${agreementText}.` : null,
  ]
    .filter(Boolean)
    .join(" ");
  const rows = s.reveal ?? [];
  const mineText = myJudgeDiffLine(rows);
  return (
    <Stack spacing={1.25} alignItems="stretch">
      <Typography
        typography="m3"
        fontWeight="fontWeightSemiBold"
        textAlign="center"
      >
        {headline}
      </Typography>
      <Typography typography="s1" color="text.secondary" textAlign="center">
        {lo != null ? (
          <>
            Likely between {pct(lo)} and {pct(hi)} (
            <Term k="ci95">95% confidence</Term>).{" "}
          </>
        ) : null}
        {s.wins} wins, {s.ties} ties, {s.losses} losses for {labelB}.
        {line.lowSample ? (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title="Low sample: fewer than 30 rows"
          >
            <Box component="span" sx={{ color: "text.disabled" }}>
              {` · low sample (${s.judged} ${s.judged === 1 ? "row" : "rows"})`}
            </Box>
          </CustomTooltip>
        ) : null}
      </Typography>
      {evidence && (
        <Typography typography="s2" color="text.secondary" textAlign="center">
          {evidence}
        </Typography>
      )}
      <Box
        role="img"
        aria-label={`Wins, ties and losses for ${labelB}: ${s.wins} wins, ${s.ties} ties, ${s.losses} losses`}
      >
        <Bar parts={parts} />
      </Box>
      <Legend
        items={[
          { label: `Wins for ${labelB}`, value: s.wins, color: WIN },
          { label: "Ties", value: s.ties, color: TIE },
          { label: `Losses for ${labelB}`, value: s.losses, color: LOSS },
        ]}
      />
      {agreement?.n ? (
        <Stack
          direction="row"
          gap={0.75}
          alignItems="center"
          justifyContent="center"
        >
          <Iconify
            icon="mdi:robot-outline"
            width={15}
            sx={{ color: "text.secondary" }}
          />
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="review-summary-agreement"
          >
            {agreementText}
          </Typography>
        </Stack>
      ) : null}
      <Stack direction="row" justifyContent="center" gap={1}>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setReveal((r) => !r)}
          aria-expanded={reveal}
          startIcon={
            <Iconify
              icon={reveal ? "mdi:eye-off-outline" : "mdi:eye-outline"}
              width={16}
            />
          }
          disabled={!rows.length}
        >
          {reveal ? "Hide your choices" : "Reveal your choices"}
        </Button>
        <CopyButton
          value={summaryText}
          tooltip="Copy summary"
          toast="Summary copied"
        />
      </Stack>
      {mineText ? (
        <Typography
          typography="s2"
          color="text.secondary"
          textAlign="center"
          data-testid="review-summary-mine"
        >
          {mineText}
        </Typography>
      ) : null}
      <Collapse in={reveal} unmountOnExit>
        <Box sx={{ maxHeight: 280, overflow: "auto" }}>
          <Table size="small" stickyHeader aria-label="Your choices, revealed">
            <TableHead>
              <TableRow>
                <TableCell>Row</TableCell>
                <TableCell>You chose</TableCell>
                <TableCell>Judge chose</TableCell>
                <TableCell align="right">Open</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.row_id} hover>
                  <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
                    {r.row_no != null ? `Row ${r.row_no}` : "Row"}
                  </TableCell>
                  <TableCell>
                    {r.my_label ??
                      (r.status === "skipped"
                        ? "Skipped"
                        : r.status === "completed"
                          ? "Another reviewer"
                          : "Not answered")}
                  </TableCell>
                  <TableCell
                    sx={{
                      color:
                        r.agree === false ? "warning.dark" : "text.primary",
                    }}
                  >
                    {r.judge_label ?? "No judge pick"}
                    {r.agree === false ? " · differs" : ""}
                  </TableCell>
                  <TableCell align="right">
                    {datasetId ? (
                      <Link
                        component={RouterLink}
                        to={taskCompareHref({
                          datasetId,
                          runIds: [s.run_a.run_id, s.run_b.run_id],
                          baselineId: s.run_a.run_id,
                          rowId: r.row_id,
                          from,
                        })}
                        underline="hover"
                        typography="s2"
                      >
                        Side by side
                      </Link>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      </Collapse>
    </Stack>
  );
}

PairwiseSummary.propTypes = {
  s: PropTypes.object.isRequired,
  datasetId: PropTypes.string,
  from: PropTypes.string,
};

const fmtMean = (l) => {
  if (l.mean == null) return "—";
  if (l.type === "thumbs_up_down") return `${pct(l.mean)} yes`;
  if (l.type === "star") return `${l.mean.toFixed(1)} / ${l.max}`;
  return l.mean.toFixed(2);
};
const fmtDelta = (l) => {
  if (l.delta == null) return null;
  const v =
    l.type === "thumbs_up_down"
      ? `${Math.round(Math.abs(l.delta) * 100)} pts`
      : Math.abs(l.delta).toFixed(1);
  return `${l.delta > 0 ? "+" : l.delta < 0 ? "−" : ""}${v}`;
};

function RubricSummary({ s }) {
  return (
    <Stack spacing={1}>
      <Typography
        typography="m3"
        fontWeight="fontWeightSemiBold"
        textAlign="center"
      >
        {s.run?.label} rated on {s.rated_items ?? s.completed_items} of{" "}
        {s.total_items} rows
      </Typography>
      {s.outside_items > 0 && (
        <Typography
          typography="s2"
          color="text.secondary"
          textAlign="center"
          data-testid="review-summary-outside-rated"
        >
          Outside reviewers rated {s.outside_items} of these rows.
        </Typography>
      )}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          overflow: "hidden",
        }}
      >
        {s.labels.map((l, i) => (
          <Stack
            key={l.label_id}
            direction="row"
            alignItems="center"
            gap={1.5}
            sx={{
              px: 1.5,
              py: 1,
              borderTop: i ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              sx={{ flex: 1 }}
            >
              {l.name}
            </Typography>
            <Typography
              typography="s1"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {fmtMean(l)}
            </Typography>
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ minWidth: 48, textAlign: "right" }}
            >
              n={l.n}
            </Typography>
            <Typography
              typography="s2"
              sx={{
                minWidth: 120,
                textAlign: "right",
                color:
                  l.delta == null
                    ? "text.disabled"
                    : l.delta > 0
                      ? "success.main"
                      : l.delta < 0
                        ? "error.main"
                        : "text.secondary",
              }}
            >
              {fmtDelta(l)
                ? `${fmtDelta(l)} vs ${s.baseline?.label}`
                : s.baseline
                  ? `${s.baseline.label} not rated`
                  : "No baseline run"}
            </Typography>
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}

RubricSummary.propTypes = { s: PropTypes.object.isRequired };

/**
 * Experiment-review summary in the workspace's "All Done!" state (§3.12):
 * pairwise win rate with its Wilson CI and W–T–L (C7), or the rubric's
 * per-label means vs the baseline run; `See results in grid` opens the
 * compare page for the pair (or the run's experiment).
 */
export default function ExperimentReviewSummary({ queueId, datasetId, from }) {
  const navigate = useNavigate();
  const { data, isLoading } = useAnnotationQueueDetail(queueId);
  const s = data?.review_summary;
  if (isLoading && !s)
    return <Skeleton variant="rounded" width={480} height={140} />;
  if (!s) return null;
  const ds = datasetId ?? data?.metadata?.dataset_id ?? data?.dataset;
  const gridUrl =
    s.mode === "pairwise"
      ? compareUrl(ds, s.run_a.run_id, s.run_b.run_id)
      : s.experiment_id
        ? `/dashboard/develop/experiment/${s.experiment_id}/data`
        : null;
  return (
    <Stack
      spacing={2}
      sx={(t) => ({
        width: 680,
        maxWidth: "calc(100vw - 48px)",
        p: 2.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        boxShadow: `0 1px 2px ${alpha(t.palette.common.black, 0.04)}`,
      })}
    >
      {s.mode === "pairwise" ? (
        <PairwiseSummary s={s} datasetId={ds} from={from} />
      ) : (
        <RubricSummary s={s} />
      )}
      {gridUrl ? (
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={() => navigate(gridUrl)}
          startIcon={<Iconify icon="mdi:table-eye" width={16} />}
          sx={{ alignSelf: "center" }}
        >
          See results in grid
        </Button>
      ) : null}
      <CorrectionsBlock datasetId={ds} queueId={queueId} />
      <OutsideReviewersSection queueId={queueId} />
    </Stack>
  );
}

ExperimentReviewSummary.propTypes = {
  queueId: PropTypes.string.isRequired,
  datasetId: PropTypes.string,
  // R3: where Side by side returns to
  from: PropTypes.string,
};
