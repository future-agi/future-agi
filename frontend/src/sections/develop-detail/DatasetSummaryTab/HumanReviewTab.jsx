import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  Divider,
  LinearProgress,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import CustomTooltip from "src/components/tooltip";
import { fmtPct, NUM_SX } from "src/sections/common/format/fmt";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import AnnotationCard from "./AnnotationCard";
import AnnotationCardLoading from "./Loaders/AnnotationCardLoading";
import JudgeHumanMatrix from "./JudgeHumanMatrix";
import { num } from "./summaryApi";

const tabular = NUM_SX;

function Stat({ label, value, sub, tip }) {
  const body = (
    <Stack gap={0.25} sx={tip ? { cursor: "help" } : undefined}>
      <Typography typography="s3" color="text.secondary">
        {label}
      </Typography>
      <Typography typography="s1" fontWeight={600} sx={tabular}>
        {value}
        {sub && (
          <Typography
            component="span"
            typography="s2"
            color="text.secondary"
            sx={{ ml: 0.5 }}
          >
            {sub}
          </Typography>
        )}
      </Typography>
    </Stack>
  );
  return tip ? (
    <CustomTooltip show size="small" placement="top" title={tip}>
      {body}
    </CustomTooltip>
  ) : (
    body
  );
}

Stat.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  sub: PropTypes.node,
  tip: PropTypes.string,
};

/**
 * Human review sub-tab: who reviewed how much, inter-annotator agreement, the
 * queue, judge-vs-human agreement, then the label charts and annotators.
 */
const HumanReviewTab = ({
  datasetId,
  summary,
  isLoading,
  version,
  evalOrder,
  setCurrentTab,
}) => {
  const navigate = useNavigate();
  if (isLoading) return <AnnotationCardLoading />;
  const review = summary?.review ?? {};
  const header = summary?.header ?? {};
  const queue = review.queue;
  const total = review.total_rows ?? 0;
  const reviewed = review.reviewed_rows ?? 0;
  const share = total ? Math.round((reviewed / total) * 100) : 0;
  const pairs = summary?.judge_agreement ?? [];

  return (
    <Stack gap={2}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          px: 2,
          py: 1.5,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={3}
          flexWrap="wrap"
          divider={<Divider orientation="vertical" flexItem />}
        >
          <Stat
            label="Reviewed rows"
            value={`${reviewed} / ${total}`}
            sub={`(${share}%)`}
            tip={`${reviewed} of the ${total} rows in view have at least one human label or a reviewer's check of the judge (any reviewer).${review.judge_review_rows ? ` ${review.judge_review_rows} of those checks are in Judge vs human below.` : ""}`}
          />
          <Stat
            label="Reviewers agree"
            value={
              header.overall_agreement == null
                ? num(null)
                : fmtPct(header.overall_agreement)
            }
            sub={
              header.overall_agreement_rows
                ? `on ${header.overall_agreement_rows} double-labelled`
                : null
            }
            tip={
              header.overall_agreement == null
                ? "Not available: needs 2 or more reviewers on the same row."
                : `Share of rows labelled by 2 or more people where they gave the same answer, averaged over labels (${header.overall_agreement_rows ?? 0} row-label pairs).${header.overall_agreement === 1 ? " 100% on so few rows says little: add a second reviewer to more rows." : ""}`
            }
          />
          <Stat
            label="Reviewers"
            value={review.annotators ?? 0}
            tip="People who labelled at least one row in view or checked the judge on it."
          />
          <Stack gap={0.25}>
            <Typography typography="s3" color="text.secondary">
              Queue
            </Typography>
            {queue ? (
              <Stack direction="row" gap={1} alignItems="baseline">
                <Typography typography="s1" fontWeight={600}>
                  {queue.name}
                </Typography>
                {queue.pending != null && (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={tabular}
                  >
                    {queue.pending} pending
                  </Typography>
                )}
                <Link
                  component={RouterLink}
                  to={paths.dashboard.annotations.queueDetail(queue.id)}
                  underline="hover"
                  typography="s2"
                  fontWeight={500}
                >
                  Open queue
                </Link>
              </Stack>
            ) : (
              <Typography typography="s2" color="text.secondary">
                No queue on this dataset
              </Typography>
            )}
          </Stack>
          <Box sx={{ flex: 1 }} />
          {queue && (
            <Stack gap={0.5} alignItems="flex-end">
              <CustomTooltip
                show
                size="small"
                placement="top"
                title={`Open the review page at the next of ${queue.pending ?? 0} pending rows`}
              >
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() =>
                    navigate(paths.dashboard.annotations.annotate(queue.id))
                  }
                  startIcon={
                    <Iconify icon="mdi:account-check-outline" width={16} />
                  }
                  sx={{ typography: "s2", fontWeight: 600 }}
                >
                  {queue.pending
                    ? `Review ${queue.pending} pending`
                    : "Open the review queue"}
                </Button>
              </CustomTooltip>
              {queue.total ? (
                <CustomTooltip
                  show
                  size="small"
                  placement="bottom"
                  title={`${queue.completed ?? 0} of ${queue.total} rows done in “${queue.name}”`}
                >
                  <Stack direction="row" alignItems="center" gap={1}>
                    <LinearProgress
                      variant="determinate"
                      value={((queue.completed ?? 0) / queue.total) * 100}
                      sx={{ width: 120, height: 6, borderRadius: 3 }}
                    />
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={tabular}
                    >
                      {fmtPct((queue.completed ?? 0) / queue.total, { dp: 0 })}
                    </Typography>
                  </Stack>
                </CustomTooltip>
              ) : null}
            </Stack>
          )}
        </Stack>
      </Box>

      {pairs.length > 0 ? (
        <JudgeHumanMatrix
          pairs={pairs}
          datasetId={datasetId}
          version={version}
          evalOrder={evalOrder}
        />
      ) : (
        <Typography typography="s2" color="text.secondary">
          No rows have both an eval score and a 👍/👎 human label yet.
        </Typography>
      )}

      <AnnotationCard
        datasetId={datasetId}
        setCurrentTab={setCurrentTab}
        summary={summary}
        showHeader={false}
        hideEmpty={pairs.length > 0}
      />
    </Stack>
  );
};

HumanReviewTab.propTypes = {
  datasetId: PropTypes.string,
  summary: PropTypes.object,
  isLoading: PropTypes.bool,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  evalOrder: PropTypes.array,
  setCurrentTab: PropTypes.func,
};

export default HumanReviewTab;
