import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import { useNavigate, useParams } from "react-router-dom";

import { hubPaths } from "./constants";
import {
  useHubMe,
  useRecheck,
  useReviewDiff,
  useReviewSubmission,
  useStartReview,
} from "./api/queries";
import { formatDateTime, timeAgo } from "./lib/format";
import CheckReport from "./components/CheckReport";
import DecisionForm from "./components/DecisionForm";
import DiffView from "./components/DiffView";
import {
  HubError,
  HubSection,
  HubSkeletonPage,
  HubState,
} from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";
import ReadmeView from "./components/ReadmeView";
import ReviewFiles from "./components/ReviewFiles";
import { StatusChip } from "./components/StatusTimeline";
import TierBadge from "./components/TierBadge";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./components/tabRow";

const TABS = [
  ["report", "Check report"],
  ["files", "Files"],
  ["readme", "README"],
  ["diff", "Changes"],
  ["log", "Run log"],
  ["manifest", "Manifest"],
];

function LockBar({ submission, meEmail }) {
  const start = useStartReview(submission.id);
  const lockedByOther =
    submission.reviewer &&
    submission.reviewer !== meEmail &&
    submission.reviewStartedAt &&
    Date.now() - new Date(submission.reviewStartedAt).getTime() <
      2 * 3600 * 1000;
  if (submission.reviewer === meEmail) {
    return (
      <Typography
        typography="s2"
        color="text.secondary"
      >{`You started this review ${timeAgo(submission.reviewStartedAt)}.`}</Typography>
    );
  }
  return (
    <Box
      sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}
    >
      {lockedByOther && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-review-lock"
        >
          {`Being reviewed by ${submission.reviewer} since ${formatDateTime(submission.reviewStartedAt)}.`}
        </Typography>
      )}
      <Button
        size="small"
        variant="outlined"
        disabled={start.isPending}
        onClick={() => start.mutate(Boolean(lockedByOther))}
        data-testid="hub-start-review"
      >
        {lockedByOther ? "Take over" : "Start review"}
      </Button>
      {start.isError && <Alert severity="error">{start.error.message}</Alert>}
    </Box>
  );
}

LockBar.propTypes = {
  submission: PropTypes.object.isRequired,
  meEmail: PropTypes.string,
};

// One submission under review (SPEC §5.2): the manifest, README, files with
// per-file findings, the full check report and run log, the diff against the
// previous version, the publisher's history, and the decision form.
export default function HubReviewPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("report");
  const me = useHubMe();
  const maintainer = Boolean(me.data?.isMaintainer);
  const review = useReviewSubmission(id, maintainer);
  // Approval depends on the diff even before the Changes tab is opened.
  const diff = useReviewDiff(id, maintainer);
  const diffStatus = useRef(null);
  const diffBusy = diff.isPending || (diff.isError && diff.isFetching);
  const recheck = useRecheck(id);
  const row = review.data;
  const back = { href: hubPaths.review, label: "Review queue" };
  const ref = row
    ? `${row.publisher}/${row.name || row.hints?.name || "…"}${row.version ? `@${row.version}` : ""}`
    : "Review";

  let body;
  if (review.isLoading) body = <HubSkeletonPage />;
  else if (review.error?.status === 404)
    body = <HubState icon="search" title="No such submission" />;
  else if (review.isError || !row)
    body = <HubError error={review.error} onRetry={() => review.refetch()} />;
  else {
    const reviewable = row.status === "in_review";
    const history = row.publisherHistory || {};
    body = (
      <>
        <HubSection testId="hub-review-header">
          <Box
            sx={{
              display: "flex",
              gap: 1,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <Typography
              typography="m3"
              fontWeight={600}
              sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
              data-testid="hub-review-ref"
            >
              {ref}
            </Typography>
            <StatusChip status={row.status} />
            <TierBadge tier={history.tier} short />
          </Box>
          <Typography typography="s2" color="text.secondary">
            {`Submitted by ${row.submittedBy?.email || "unknown"} via ${row.channel} · ${timeAgo(row.createdAt)}${row.previousVersion ? ` · update of ${row.previousVersion.version}` : " · new entry"}${row.remixOf ? ` · remix of ${row.remixOf}` : ""}`}
          </Typography>
          {row.notes && (
            <Typography
              typography="s2"
              color="text.primary"
            >{`Version notes: ${row.notes}`}</Typography>
          )}
          {reviewable && (
            <LockBar submission={row} meEmail={me.data?.user?.email} />
          )}
          {(row.status === "in_review" || row.status === "check_error") && (
            <Box>
              <Button
                size="small"
                color="inherit"
                disabled={recheck.isPending}
                onClick={() => recheck.mutate()}
                sx={{ color: "text.secondary", px: 0 }}
              >
                Run the checks again
              </Button>
            </Box>
          )}
        </HubSection>
        <Box
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              lg: "minmax(0, 1fr) 400px",
            },
            alignItems: "start",
          }}
        >
          <Box
            sx={{
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            <Tabs
              value={tab}
              onChange={(_, next) => setTab(next)}
              {...TAB_ROW_PROPS}
              sx={{
                borderBottom: "1px solid",
                borderColor: "divider",
                minHeight: 40,
                "& .MuiTab-root": { minHeight: 40 },
                ...TAB_ROW_SCROLL_SX,
              }}
            >
              {TABS.map(([value, label]) => (
                <Tab
                  key={value}
                  value={value}
                  label={
                    value === "report" && row.flags?.length
                      ? `${label} (${row.flags.length} flags)`
                      : label
                  }
                  data-testid={`hub-review-tab-${value}`}
                />
              ))}
            </Tabs>
            {tab === "report" && <CheckReport report={row.report} />}
            {tab === "files" && <ReviewFiles submission={row} />}
            {tab === "readme" &&
              (row.readme ? (
                <ReadmeView markdown={row.readme} />
              ) : (
                <Typography typography="s2" color="text.secondary">
                  No README.
                </Typography>
              ))}
            {tab === "diff" && (
              <Box
                ref={diffStatus}
                tabIndex={-1}
                role={diffBusy ? "status" : undefined}
              >
                {diffBusy ? (
                  <>
                    <Typography typography="s2" color="text.secondary">
                      The changes are still loading.
                    </Typography>
                    {diff.isPending && <HubSkeletonPage />}
                  </>
                ) : diff.isError ? (
                  <HubError
                    compact
                    title="The changes did not load"
                    error={diff.error}
                    onRetry={(event) => {
                      if (document.activeElement === event.currentTarget)
                        diffStatus.current?.focus();
                      diff.refetch();
                    }}
                  />
                ) : (
                  <DiffView
                    diff={diff.data?.diff}
                    previous={row.previousVersion?.version}
                  />
                )}
              </Box>
            )}
            {tab === "log" && (
              <Box
                component="pre"
                data-testid="hub-run-log"
                sx={{
                  m: 0,
                  p: 1.5,
                  maxHeight: 520,
                  overflow: "auto",
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                }}
              >
                {row.log || "No run log."}
              </Box>
            )}
            {tab === "manifest" && (
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1.5,
                  maxHeight: 520,
                  overflow: "auto",
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                  fontSize: 12,
                }}
              >
                {JSON.stringify(row.manifest, null, 2)}
              </Box>
            )}
          </Box>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <HubSection title="Publisher" testId="hub-review-publisher">
              <Typography typography="s2" color="text.secondary">
                {`@${history.handle} · ${history.entryCount ?? 0} listed entr${history.entryCount === 1 ? "y" : "ies"} · ${history.pastRejections ?? 0} past rejection${history.pastRejections === 1 ? "" : "s"} · ${history.openReports ?? 0} open report${history.openReports === 1 ? "" : "s"}`}
              </Typography>
            </HubSection>
            {reviewable ? (
              <DecisionForm
                key={row.id}
                submission={row}
                approveDisabled={diff.isPending || diff.isError}
                diffError={diff.isError ? diff.error : null}
                diffLoading={diff.isFetching}
                onRetryDiff={() => diff.refetch()}
                onDecided={() =>
                  navigate(hubPaths.review, {
                    replace: true,
                    state: { decided: ref },
                  })
                }
              />
            ) : (
              <HubSection title="Decision">
                <Typography typography="s2" color="text.secondary">
                  {row.statusText}
                </Typography>
              </HubSection>
            )}
          </Box>
        </Box>
      </>
    );
  }

  return (
    <MaintainerGate
      title="Review"
      documentTitle={ref}
      back={back}
      testId="hub-review"
    >
      {body}
    </MaintainerGate>
  );
}
