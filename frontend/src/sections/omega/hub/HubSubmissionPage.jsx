import React from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { useParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";

import { hubPaths } from "./constants";
import { useSubmission, useWithdraw } from "./api/queries";
import { formatDateTime, splitRef } from "./lib/format";
import { WITHDRAWABLE } from "./lib/status";
import ChangeNotesList from "./components/ChangeNotesList";
import CheckReport from "./components/CheckReport";
import DisclosurePanel from "./components/DisclosurePanel";
import {
  StepStatus,
  orderedSteps,
  stepLabel,
} from "./components/EvidencePanel";
import HubFrame from "./components/HubFrame";
import {
  HubError,
  HubSection,
  HubSkeletonPage,
  HubState,
} from "./components/HubStates";
import StatusTimeline, {
  StatusChip,
  StepTracker,
} from "./components/StatusTimeline";

function title(row) {
  const name = row.name || row.hints?.name;
  const version = row.version || row.hints?.version;
  return name
    ? `${row.publisher}/${name}${version ? `@${version}` : ""}`
    : `${row.publisher}/… (reading the manifest)`;
}

// One submission, live (SPEC §3.1): where it is in the pipeline, what the
// submitter may see at that state, and what to do next.
export default function HubSubmissionPage() {
  const { id } = useParams();
  const submission = useSubmission(id);
  const withdraw = useWithdraw(id);
  const row = submission.data;
  const back = { href: hubPaths.submissions, label: "My submissions" };

  if (submission.isLoading) {
    return (
      <HubFrame back={back} documentTitle="Submission">
        <HubSkeletonPage />
      </HubFrame>
    );
  }
  if (submission.error?.status === 404) {
    return (
      <HubFrame back={back} documentTitle="Submission">
        <HubState
          icon="search"
          title="No such submission"
          description="It does not exist, or it is not yours to see."
        />
      </HubFrame>
    );
  }
  if (submission.isError || !row) {
    return (
      <HubFrame back={back} documentTitle="Submission">
        <HubError
          error={submission.error}
          onRetry={() => submission.refetch()}
        />
      </HubFrame>
    );
  }

  const decision = row.decision;
  const published = row.published ? splitRef(row.published) : null;
  const steps = row.checkSummary?.steps || {};
  return (
    <HubFrame
      back={back}
      title={
        <Box component="span" sx={{ fontFamily: "monospace" }}>
          {title(row)}
        </Box>
      }
      documentTitle={title(row)}
      description={`Submitted ${formatDateTime(row.createdAt)} through the ${row.channel === "web" ? "web upload" : row.channel === "cli" ? "Omega CLI" : "GitHub channel"}`}
      action={<StatusChip status={row.status} />}
      testId="hub-submission"
    >
      <HubSection testId="hub-submission-status">
        <StepTracker status={row.status} />
        <Typography
          typography="s1"
          color="text.primary"
          data-testid="hub-status-text"
        >
          {row.statusText}
        </Typography>
        {(row.status === "received" ||
          row.status === "checking" ||
          row.status === "in_review") && (
          <Typography typography="s3" color="text.tertiary">
            This page updates on its own.
          </Typography>
        )}
      </HubSection>

      {(row.status === "changes_requested" || row.status === "rejected") &&
        decision && (
          <HubSection
            title={
              row.status === "changes_requested"
                ? "Changes requested by a Future AGI maintainer"
                : "Not accepted"
            }
            testId="hub-decision"
          >
            <Typography
              typography="s2"
              color="text.primary"
              sx={{ whiteSpace: "pre-wrap" }}
              data-testid="hub-decision-reason"
            >
              {decision.reason}
            </Typography>
            <ChangeNotesList notes={decision.notes} />
            {row.status === "changes_requested" && (
              <Box
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "center",
                  flexWrap: "wrap",
                }}
              >
                <Typography typography="s2" color="text.secondary">
                  Upload a fixed version; it goes through the checks and a fresh
                  review.
                </Typography>
                <Button
                  component={RouterLink}
                  href={hubPaths.publish}
                  size="small"
                  variant="contained"
                  data-testid="hub-resubmit"
                >
                  Upload a new version
                </Button>
              </Box>
            )}
          </HubSection>
        )}

      {row.status === "checks_failed" && (
        <HubSection title="What the checks found" testId="hub-findings">
          <Typography typography="s2" color="text.secondary">
            This upload is not sent to maintainers. Each finding names its file
            and the fix.
          </Typography>
          <CheckReport report={row.report} onlyProblems />
        </HubSection>
      )}

      {row.status === "approved" && (
        <>
          <Alert severity="success" data-testid="hub-published">
            {"Published as "}
            {published ? (
              <Link
                component={RouterLink}
                href={hubPaths.entry(published.publisher, published.name)}
                fontWeight={600}
              >
                {row.published}
              </Link>
            ) : (
              row.published
            )}
          </Alert>
          <DisclosurePanel
            disclosure={row.confirmedDisclosure}
            title="Disclosure as published"
          />
        </>
      )}

      {Object.keys(steps).length > 0 && row.status !== "checks_failed" && (
        <HubSection title="Automated checks" testId="hub-check-summary">
          <Box
            component="ul"
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              display: "grid",
              gap: 0.5,
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
              columnGap: 3,
            }}
          >
            {orderedSteps(steps).map(([step, status]) => (
              <Box
                component="li"
                key={step}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 1,
                }}
              >
                <Typography typography="s2" color="text.secondary">
                  {stepLabel(step)}
                </Typography>
                <StepStatus status={status === "flag" ? "flag" : status} />
              </Box>
            ))}
          </Box>
          {row.checkSummary?.flagsReviewed > 0 && (
            <Typography typography="s3" color="text.tertiary">
              {`${row.checkSummary.flagsReviewed} flagged call${row.checkSummary.flagsReviewed === 1 ? "" : "s"} for the reviewer to read.`}
            </Typography>
          )}
        </HubSection>
      )}

      {row.notes && (
        <HubSection title="Your version notes">
          <Typography typography="s2" color="text.secondary">
            {row.notes}
          </Typography>
        </HubSection>
      )}

      <HubSection title="History">
        <StatusTimeline timeline={row.timeline} />
      </HubSection>

      {WITHDRAWABLE.includes(row.status) && (
        <Box>
          <Button
            color="inherit"
            size="small"
            variant="outlined"
            disabled={withdraw.isPending}
            onClick={() => withdraw.mutate()}
            data-testid="hub-withdraw"
            sx={{ borderColor: "divider" }}
          >
            Withdraw this submission
          </Button>
          {withdraw.isError && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {withdraw.error.message}
            </Alert>
          )}
        </Box>
      )}
    </HubFrame>
  );
}
