import React, { useId } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";

import { hubPaths } from "./constants";
import { useMySubmissions, useSubmission, useWithdraw } from "./api/queries";
import { formatStamp } from "./lib/decision";
import { splitRef, timeAgo } from "./lib/format";
import { WITHDRAWABLE } from "./lib/status";
import { answeringUpload, nextStep } from "./lib/submissionViews";
import {
  noCheckerStatus,
  selfHostedAudience,
  stalledWithoutChecker,
  useUploadsChecked,
  useUploadsOffAudience,
} from "./lib/uploadsChecked";
import ChangeNotesList from "./components/ChangeNotesList";
import CheckReport, { CheckSteps } from "./components/CheckReport";
import { CopyButton } from "./components/CopyCommand";
import DisclosurePanel from "./components/DisclosurePanel";
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
  TimeAgo,
} from "./components/StatusTimeline";
import { uploadsUnavailableCopy } from "./components/UploadsUnavailable";

// Statuses whose checks have not answered, so the manifest may still be read.
const UNANSWERED = new Set(["received", "checking", "check_error"]);

// The submission's reference. With no name, the publisher, and while the
// checks have not answered, that the manifest is not read yet: true whether
// or not a checker runs to read it. Once they have answered, or the upload
// is withdrawn, no read is coming, so the title promises none.
function title(row) {
  const name = row.name || row.hints?.name;
  const version = row.version || row.hints?.version;
  if (name) return `${row.publisher}/${name}${version ? `@${version}` : ""}`;
  return UNANSWERED.has(row.status)
    ? `${row.publisher}/… (manifest not read yet)`
    : `${row.publisher}/…`;
}

// The ids the findings of a failed upload carry, so each failed check in
// the Automated checks list links down to its own.
const FINDINGS_ANCHOR = "hub-check-";

// The answer to a failed or change-requested upload: a new version, from
// the Publish page. While this server runs no Hub checker it stays a link,
// as each row's Upload a new version on My submissions does, and carries the
// page's line that uploads are off (`offId`, its id) as its description.
function UploadNewVersion({ testId, offId }) {
  return (
    <Button
      component={RouterLink}
      href={hubPaths.publish}
      size="small"
      variant="contained"
      aria-describedby={offId}
      data-testid={testId}
    >
      Upload a new version
    </Button>
  );
}

UploadNewVersion.propTypes = {
  testId: PropTypes.string.isRequired,
  offId: PropTypes.string,
};

// Beside the action at the top right, the line is as wide as the status and
// the action above it and never wider, so it takes no room from the title:
// it wraps at their width, at their edge.
const OFF_LINE_END_SX = {
  width: 0,
  minWidth: "100%",
  textAlign: { xs: "left", sm: "right" },
};

// The page's one line that uploads are off, in the words the notice above
// My submissions gives this person, beside the first Upload a new version
// (`end`: the one at the top right). Each Upload a new version on the page
// points at its id.
function UploadsOffLine({ id, text, end }) {
  return (
    <Typography
      id={id}
      typography="s2"
      color="text.secondary"
      data-testid="hub-submission-uploads-off"
      sx={end ? OFF_LINE_END_SX : undefined}
    >
      {text}
    </Typography>
  );
}

UploadsOffLine.propTypes = {
  id: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  end: PropTypes.bool,
};

// The line under a section's words that says what to do, with the way to
// do it beside it, and under them the page's line that uploads are off
// (`off`) when this is where it goes.
function Resubmit({ children, offId, off }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          {children}
        </Typography>
        <UploadNewVersion testId="hub-resubmit" offId={offId} />
      </Box>
      {off}
    </Box>
  );
}

Resubmit.propTypes = {
  children: PropTypes.node,
  offId: PropTypes.string,
  off: PropTypes.node,
};

// The top right: the status and the next action on one line, and under
// them, at the same edge, the page's line that uploads are off.
const HEADER_ACTION_SX = {
  display: "flex",
  flexDirection: "column",
  alignItems: { xs: "flex-start", sm: "flex-end" },
  gap: 0.5,
};

// One fact about the submission: a quiet label over its value, as wide as
// its value.
function Property({ label, testId, children }) {
  return (
    <Box sx={{ flex: "0 1 auto", minWidth: 0 }}>
      <Box component="dt" sx={{ typography: "s3", color: "text.secondary" }}>
        {label}
      </Box>
      <Box
        component="dd"
        data-testid={testId}
        sx={{
          m: 0,
          minWidth: 0,
          minHeight: 28,
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          typography: "s2",
          color: "text.primary",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

Property.propTypes = {
  label: PropTypes.string.isRequired,
  testId: PropTypes.string,
  children: PropTypes.node,
};

// Who moves the submission next. When a newer upload of the same entry
// answers this one, a link opens that upload. While My submissions, which
// says whether one does, is on its way, the value is a placeholder.
function NextStepFact({ next, newer, deciding }) {
  if (deciding) return <Skeleton variant="text" width={96} />;
  const yours = next.who === "you";
  return (
    <>
      <Box component="span" sx={{ fontWeight: yours ? 600 : 500 }}>
        {next.label}
      </Box>
      {newer && (
        <Link
          component={RouterLink}
          href={hubPaths.submission(newer.id)}
          sx={{ ml: 0.5 }}
        >
          See the newer upload
        </Link>
      )}
    </>
  );
}

NextStepFact.propTypes = {
  next: PropTypes.object.isRequired,
  newer: PropTypes.object,
  deciding: PropTypes.bool,
};

// One submission, live (SPEC §3.1): where it is in the pipeline, who moves
// it next, what the submitter may see at that state, and what to do next.
// When the next move is the submitter's (checks failed, changes requested),
// Upload a new version sits at the top right and again where the findings
// or the maintainer's reasons are. One that a newer upload of the same entry
// answers says so, as its row in My submissions does (lib/submissionViews.js
// over the same list), links to that upload, and has no action at the top
// right. Each automated check shows its state and time; a failed one links
// down to its findings. While this server runs no Hub checker, nothing
// claims a check job, so a received, checking or check_error submission
// cannot move: its status says why (lib/uploadsChecked.js), its chip never
// says "Checks running", it waits on an administrator on a self-hosted server
// (on Cloud, and while the server's mode is not known, it names no next step),
// and the page stops asking for it every 3 s. While it runs none, Publish takes
// no upload: each Upload a new version stays a link, as its row's does on My
// submissions, and carries the line that says so, in the words that row's
// notice uses, shown once beside the first of them.
export default function HubSubmissionPage() {
  const { id } = useParams();
  // Read the way the Publish page reads it (OMEGA_PART HUB_PUBLISH).
  const uploads = useUploadsChecked();
  const uploadsOff = uploads === "unchecked";
  // Who reads which words while no checker runs (lib/uploadsChecked.js).
  const audience = useUploadsOffAudience();
  const offCopy = uploadsUnavailableCopy(audience);
  const offId = useId();
  const submission = useSubmission(id, { live: uploads !== "unchecked" });
  // My submissions, read as its page reads it, so the two share one answer.
  const mine = useMySubmissions(undefined, { live: uploads !== "unchecked" });
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
  const stalled = stalledWithoutChecker(uploads, row.status);
  const polled =
    !stalled && ["received", "checking", "in_review"].includes(row.status);
  // A failed or change-requested upload is the publisher's to answer unless
  // a newer upload of the same entry already has. Until My submissions says
  // which, the page names no next step and offers no action for it.
  const newer = mine.data ? answeringUpload(mine.data)(row) : null;
  const next = nextStep(row, {
    stalled,
    answered: Boolean(newer),
    selfHosted: selfHostedAudience(audience),
  });
  const deciding = next.who === "you" && mine.isLoading;
  const yours = next.who === "you" && !deciding;
  // While uploads are off, each Upload a new version points at the line that
  // says so, shown once: beside the one at the top right, or, without it,
  // beside the one where the findings or the maintainer's reasons are.
  const offFor = uploadsOff ? offId : undefined;
  const offLine = (end) =>
    uploadsOff && <UploadsOffLine id={offId} text={offCopy.title} end={end} />;
  const resubmitOff = yours ? null : offLine(false);
  const summarySteps = Object.keys(row.checkSummary?.steps || {});
  const hasChecks = Boolean(row.report?.steps?.length || summarySteps.length);
  const channel =
    row.channel === "web"
      ? "web uploader"
      : row.channel === "cli"
        ? "Omega CLI"
        : "GitHub channel";
  return (
    <HubFrame
      back={back}
      title={
        <Box component="span" sx={{ fontFamily: "monospace" }}>
          {title(row)}
        </Box>
      }
      documentTitle={title(row)}
      description={`Submitted ${formatStamp(row.createdAt)} (${timeAgo(row.createdAt)}) through the ${channel}`}
      action={
        <Box sx={HEADER_ACTION_SX}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
            <StatusChip
              status={row.status}
              stalled={stalled || Boolean(row.checksStopped)}
            />
            {yours && (
              <UploadNewVersion testId="hub-next-action" offId={offFor} />
            )}
          </Box>
          {yours && offLine(true)}
        </Box>
      }
      testId="hub-submission"
    >
      <HubSection testId="hub-submission-status">
        <StepTracker status={row.status} />
        <Typography
          typography="s1"
          color="text.primary"
          data-testid="hub-status-text"
        >
          {stalled ? noCheckerStatus(audience) : row.statusText}
        </Typography>
        {/* One row of facts, each as wide as its value, that wraps only
            when the page is too narrow for it: the id is never cut. */}
        <Box
          component="dl"
          sx={{
            m: 0,
            display: "flex",
            flexWrap: "wrap",
            columnGap: 4,
            rowGap: 1,
          }}
        >
          {next.label && (
            <Property label="Next step" testId="hub-next-step">
              <NextStepFact next={next} newer={newer} deciding={deciding} />
            </Property>
          )}
          <Property label="Last update">
            <TimeAgo iso={row.updatedAt || row.createdAt} label="Updated" />
          </Property>
          <Property label="Submission id">
            <Box
              component="span"
              title={row.id}
              sx={{
                fontFamily: "monospace",
                minWidth: 0,
                overflowWrap: "anywhere",
              }}
            >
              {row.id}
            </Box>
            <CopyButton
              text={row.id}
              label="Copy the submission id"
              tooltip="Copy the id"
            />
          </Property>
        </Box>
        {polled && (
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
              <Resubmit offId={offFor} off={resubmitOff}>
                Upload a fixed version; it goes through the checks and a fresh
                review.
              </Resubmit>
            )}
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

      {hasChecks && (
        <HubSection title="Automated checks" testId="hub-check-summary">
          <CheckSteps
            report={row.report}
            summary={row.checkSummary}
            anchorPrefix={
              row.status === "checks_failed" ? FINDINGS_ANCHOR : undefined
            }
          />
          {row.checkSummary?.flagsReviewed > 0 && (
            <Typography typography="s3" color="text.tertiary">
              {`${row.checkSummary.flagsReviewed} flagged call${row.checkSummary.flagsReviewed === 1 ? "" : "s"} for the reviewer to read.`}
            </Typography>
          )}
        </HubSection>
      )}

      {row.status === "checks_failed" && (
        <HubSection title="What the checks found" testId="hub-findings">
          <Typography typography="s2" color="text.secondary">
            This upload is not sent to maintainers. Fix what the checks found
            and upload a new version.
          </Typography>
          <CheckReport
            report={row.report}
            onlyProblems
            anchorPrefix={FINDINGS_ANCHOR}
          />
          <Resubmit offId={offFor} off={resubmitOff}>
            Upload a fixed version; it goes through the checks again.
          </Resubmit>
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
