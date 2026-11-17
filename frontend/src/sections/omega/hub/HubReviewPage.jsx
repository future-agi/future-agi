import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useNavigate, useParams } from "react-router-dom";

import { KIND_LABEL, hubPaths } from "./constants";
import {
  useHubMe,
  useRecheck,
  useReviewDiff,
  useReviewSubmission,
  useStartReview,
} from "./api/queries";
import { flagGroups, formatStamp } from "./lib/decision";
import { formatDateTime, timeAgo } from "./lib/format";
import {
  NO_CHECKER_RUNNING,
  noCheckerStatus,
  stalledWithoutChecker,
  useUploadsChecked,
  useUploadsOffAudience,
} from "./lib/uploadsChecked";
import CheckReport, { CheckSummary } from "./components/CheckReport";
import DecisionForm from "./components/DecisionForm";
import DiffView from "./components/DiffView";
import EntryCover from "./components/EntryCover";
import FlagGroups from "./components/FlagGroups";
import HubPanel from "./components/HubPanel";
import { HubError, HubSkeletonPage, HubState } from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";
import ReadmeView from "./components/ReadmeView";
import ReviewFiles from "./components/ReviewFiles";
import { StatusChip } from "./components/StatusTimeline";
import TierBadge from "./components/TierBadge";
import ToneChip from "./components/ToneChip";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./components/tabRow";

const TABS = [
  ["report", "Check report"],
  ["files", "Files"],
  ["readme", "README"],
  ["diff", "Changes"],
  ["log", "Run log"],
  ["manifest", "Manifest"],
];

// Where a submission came in. Any other stored channel shows as recorded.
const SOURCE = { github: "GitHub", web: "Web upload" };

// One count of the publisher's record in words: "No past rejections" rather
// than a zero, "1 listed entry", "3 listed entries". A count the server did
// not send is left out, never shown as none.
function record(count, one, many) {
  if (typeof count !== "number") return "";
  return count ? `${count} ${count === 1 ? one : many}` : `No ${many}`;
}

const VISUALLY_HIDDEN = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

const PANEL_SX = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  bgcolor: "background.paper",
  p: 2,
  display: "flex",
  flexDirection: "column",
  gap: 1.5,
  minWidth: 0,
};

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

function Meta({ label, children }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography component="dt" typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        component="dd"
        typography="s1"
        color="text.primary"
        sx={{ m: 0, overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}

Meta.propTypes = { label: PropTypes.string, children: PropTypes.node };

const EMAIL_SX = {
  display: "inline-flex",
  maxWidth: "100%",
  minWidth: 0,
  verticalAlign: "top",
  whiteSpace: "nowrap",
  borderRadius: 0.5,
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: 2,
  },
};
const EMAIL_START_SX = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const EMAIL_DOMAIN_SX = {
  flexShrink: 0,
  maxWidth: "100%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

// The submitter's e-mail on one line. Where it is too long for its column,
// its start gives way to an ellipsis and its @domain stays whole. The whole
// address is its accessible name and its title, and shows in a tooltip on
// hover and on keyboard focus.
function SubmitterEmail({ email }) {
  const at = email.lastIndexOf("@");
  const start = at > 0 ? email.slice(0, at) : email;
  const domain = at > 0 ? email.slice(at) : "";
  return (
    <Tooltip title={email} describeChild>
      <Box
        component="span"
        tabIndex={0}
        aria-label={email}
        data-testid="hub-review-submitter"
        sx={EMAIL_SX}
      >
        <Box component="span" sx={EMAIL_START_SX}>
          {start}
        </Box>
        {domain && (
          <Box component="span" sx={EMAIL_DOMAIN_SX}>
            {domain}
          </Box>
        )}
      </Box>
    </Tooltip>
  );
}

SubmitterEmail.propTypes = { email: PropTypes.string.isRequired };

// Who sent this version, when, through which source, and which checker
// version judged it: the audit line a reviewer signs off against.
function ReviewHeader({ row, refText, stalled, tier, meEmail }) {
  const manifest = row.manifest || {};
  const checker = row.report?.checker?.version;
  return (
    <Box data-testid="hub-review-header" sx={PANEL_SX}>
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
        {manifest.cover && <EntryCover cover={manifest.cover} size={40} />}
        <Box
          sx={{
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            gap: 0.75,
          }}
        >
          <Typography
            typography="m3"
            fontWeight={600}
            sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
            data-testid="hub-review-ref"
          >
            {refText}
          </Typography>
          <Box
            sx={{
              display: "flex",
              gap: 0.75,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <StatusChip
              status={row.status}
              stalled={stalled || Boolean(row.checksStopped)}
            />
            <TierBadge tier={tier} short />
            <ToneChip
              label={
                row.previousVersion
                  ? `Update of ${row.previousVersion.version}`
                  : "New entry"
              }
            />
            {manifest.kind && (
              <ToneChip label={KIND_LABEL[manifest.kind] || manifest.kind} />
            )}
            {row.remixOf && <ToneChip label={`Remix of ${row.remixOf}`} />}
          </Box>
        </Box>
      </Box>
      <Box
        component="dl"
        sx={{
          m: 0,
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: "repeat(4, minmax(0, 1fr))",
          },
        }}
      >
        <Meta label="Submitted by">
          {row.submittedBy?.email ? (
            <SubmitterEmail email={row.submittedBy.email} />
          ) : (
            "Unknown"
          )}
        </Meta>
        <Meta label="Submitted">
          <Box component="time" dateTime={row.createdAt}>
            {formatStamp(row.createdAt)}
          </Box>
          {/* A no-break space ties the dot to the time, so a narrow column
              breaks the line after the dot, never before it. */}
          {"\u00a0· "}
          <Box component="span" sx={{ whiteSpace: "nowrap" }}>
            {timeAgo(row.createdAt)}
          </Box>
        </Meta>
        <Meta label="Source">{SOURCE[row.channel] || row.channel}</Meta>
        <Meta label="Checker">{checker || "Not recorded"}</Meta>
      </Box>
      {row.notes && (
        <Typography
          typography="s1"
          color="text.primary"
        >{`Version notes: ${row.notes}`}</Typography>
      )}
      {row.status === "in_review" && (
        <LockBar submission={row} meEmail={meEmail} />
      )}
    </Box>
  );
}

ReviewHeader.propTypes = {
  row: PropTypes.object.isRequired,
  refText: PropTypes.string.isRequired,
  stalled: PropTypes.bool,
  tier: PropTypes.string,
  meEmail: PropTypes.string,
};

// The order a reviewer reads the page's parts in, at every width: the check
// report's summary, the flags, the submission's files and logs (`details`),
// the disclosure and the checklist (`confirm`), and the Decision card with
// the publisher (`side`). Each flag is answered with its evidence just below
// it, and the decision comes after everything it confirms. Below the lg
// breakpoint (a 1024 window with the sidebar) the page is one column in this
// order; from lg the side is a column of its own beside the others, sticky
// while they scroll.
const ORDER = ["summary", "flags", "details", "confirm", "side"];

// The page's grid, its parts siblings in reading order. A part a page leaves
// out (a decided item has no disclosure or checklist) takes no row.
function ReviewGrid({ summary, flags, confirm, details, side, bar }) {
  const parts = { summary, flags, details, confirm };
  const shown = (name) => name === "side" || Boolean(parts[name]);
  const order = ORDER.filter(shown);
  const rows = order.filter((name) => name !== "side");
  return (
    <>
      <Box
        sx={{
          display: "grid",
          gap: 3,
          alignItems: "start",
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            lg: "minmax(0, 1fr) 360px",
          },
          gridTemplateAreas: {
            lg: rows.map((name) => `"${name} side"`).join(" "),
          },
          // A side column taller than the parts beside it lengthens the
          // last row, never the gaps between them.
          gridTemplateRows: {
            lg: [...rows.slice(1).map(() => "auto"), "1fr"].join(" "),
          },
        }}
      >
        {order.map((name) =>
          name === "side" ? (
            <Box
              key={name}
              component="aside"
              aria-label="Decision and publisher"
              sx={{
                gridArea: { lg: "side" },
                minWidth: 0,
                display: "flex",
                flexDirection: "column",
                gap: 2,
                position: { lg: "sticky" },
                top: { lg: 16 },
                maxHeight: { lg: "calc(100vh - 32px)" },
                overflowY: { lg: "auto" },
              }}
            >
              {side}
            </Box>
          ) : (
            <Box
              key={name}
              sx={{
                gridArea: { lg: name },
                minWidth: 0,
                display: "flex",
                flexDirection: "column",
                gap: 3,
              }}
            >
              {parts[name]}
            </Box>
          ),
        )}
      </Box>
      {bar}
    </>
  );
}

ReviewGrid.propTypes = {
  summary: PropTypes.node,
  flags: PropTypes.node,
  confirm: PropTypes.node,
  details: PropTypes.node,
  side: PropTypes.node,
  bar: PropTypes.node,
};

// One submission under review (SPEC §5.2): who sent it and when, the check
// report's verdict, the flags grouped by rule, then the files with per-file
// findings, the README, the diff against the previous version, the run log
// and the manifest, then the disclosure and the checklist, with the decision
// beside them from the lg breakpoint and after them below it (ReviewGrid).
export default function HubReviewPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  // The details sit just under the check report's summary and the flags, so
  // they open on Files, and the report is never drawn twice on one page; its
  // tab still holds the whole report.
  const [tab, setTab] = useState("files");
  const [filePath, setFilePath] = useState(null);
  const detailsRef = useRef(null);
  const detailsTitleId = useId();
  const me = useHubMe();
  const maintainer = Boolean(me.data?.isMaintainer);
  const review = useReviewSubmission(id, maintainer);
  // Approval depends on the diff even before the Changes tab is opened.
  const diff = useReviewDiff(id, maintainer);
  const diffStatus = useRef(null);
  const diffBusy = diff.isPending || (diff.isError && diff.isFetching);
  const recheck = useRecheck(id);
  // Read the way the Publish page reads it (OMEGA_PART HUB_PUBLISH). The
  // recheck stays off until the answer says a checker runs.
  const uploads = useUploadsChecked();
  const noChecker = uploads === "unchecked";
  // While this server runs no Hub checker, nothing claims a check job: a
  // recheck would move the submission to Received for good, and a received,
  // checking or check_error one cannot move (omega_hub/services/checks.py).
  // The recheck's note says why (NO_CHECKER_RUNNING), and the status line
  // says it in the words for the person signed in, on this server
  // (noCheckerStatus): both from lib/uploadsChecked.js.
  const audience = useUploadsOffAudience();
  const row = review.data;
  const back = { href: hubPaths.review, label: "Review queue" };
  const ref = row
    ? `${row.publisher}/${row.name || row.hints?.name || "…"}${row.version ? `@${row.version}` : ""}`
    : "Review";
  // A flag's place opens its file in Files and moves focus to the files.
  const openFile = (path) => {
    setFilePath(path);
    setTab("files");
    detailsRef.current?.scrollIntoView?.({ block: "start" });
    detailsRef.current?.focus({ preventScroll: true });
  };

  let body;
  if (review.isLoading) body = <HubSkeletonPage />;
  else if (review.error?.status === 404)
    body = <HubState icon="search" title="No such submission" />;
  else if (review.isError || !row)
    body = <HubError error={review.error} onRetry={() => review.refetch()} />;
  else {
    const reviewable = row.status === "in_review";
    const stalled = stalledWithoutChecker(uploads, row.status);
    const history = row.publisherHistory || {};
    const groups = flagGroups(row.flags, row.report);
    const flagCount = (row.flags || []).length;
    const canRecheck =
      row.status === "in_review" || row.status === "check_error";
    // Off but focusable: no checker runs, or a recheck is on its way.
    const recheckOff = noChecker || recheck.isPending;
    const recheckBlock = canRecheck && (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: 0.5,
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {/* With no checker running, or while a recheck is on its way, the
            recheck is off but stays in the tab order (aria-disabled, the
            disabled colour, no hover), and a press sends nothing: with no
            checker its note is heard as its description, and a keyboard user
            who pressed it keeps their place. While the answer loads it is
            disabled: there is no note to hear then. */}
        <Button
          size="small"
          color="inherit"
          disabled={!recheckOff && uploads !== "checked"}
          aria-disabled={recheckOff || undefined}
          disableRipple={recheckOff}
          onClick={recheckOff ? undefined : () => recheck.mutate()}
          aria-describedby={noChecker ? "hub-recheck-no-checker" : undefined}
          sx={{
            color: recheckOff ? "action.disabled" : "text.secondary",
            px: 0,
            ...(recheckOff && {
              cursor: "default",
              "&:hover": { bgcolor: "transparent" },
            }),
          }}
          data-testid="hub-recheck"
        >
          Run the checks again
        </Button>
        {noChecker && (
          <Typography
            id="hub-recheck-no-checker"
            typography="s2"
            color="text.secondary"
            data-testid="hub-recheck-no-checker"
          >
            {NO_CHECKER_RUNNING}
          </Typography>
        )}
        {recheck.isError && (
          <Alert severity="error" sx={{ alignSelf: "stretch" }}>
            {recheck.error.message}
            {recheck.error.fix ? ` ${recheck.error.fix}` : ""}
          </Alert>
        )}
      </Box>
    );
    const summary = (
      <CheckSummary
        report={row.report}
        flagCount={flagCount}
        groupCount={groups.length}
        answerable={reviewable}
        action={recheckBlock}
      />
    );
    const counts = [
      record(history.entryCount, "listed entry", "listed entries"),
      record(history.pastRejections, "past rejection", "past rejections"),
      record(history.openReports, "open report", "open reports"),
    ].filter(Boolean);
    const publisher = (
      <HubPanel title="Publisher" testId="hub-review-publisher">
        <Typography typography="s1" color="text.primary">
          {`@${history.handle || row.publisher}`}
        </Typography>
        {counts.length > 0 && (
          <Typography typography="s2" color="text.secondary">
            {counts.join(" · ")}
          </Typography>
        )}
      </HubPanel>
    );
    const details = (
      <Box
        component="section"
        ref={detailsRef}
        tabIndex={-1}
        aria-labelledby={detailsTitleId}
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          minWidth: 0,
          "&:focus": { outline: "none" },
        }}
      >
        <Typography component="h2" id={detailsTitleId} sx={VISUALLY_HIDDEN}>
          Submission details
        </Typography>
        <Tabs
          value={tab}
          onChange={(_, next) => setTab(next)}
          aria-labelledby={detailsTitleId}
          {...TAB_ROW_PROPS}
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
            ...TAB_ROW_SCROLL_SX,
          }}
        >
          {TABS.map(([value, label]) => (
            <Tab
              key={value}
              value={value}
              label={
                value === "report" && flagCount
                  ? `${label} (${flagCount} flag${flagCount === 1 ? "" : "s"})`
                  : label
              }
              data-testid={`hub-review-tab-${value}`}
            />
          ))}
        </Tabs>
        {tab === "report" && <CheckReport report={row.report} />}
        {tab === "files" && (
          <ReviewFiles
            submission={row}
            selected={filePath}
            onSelect={setFilePath}
          />
        )}
        {tab === "readme" &&
          (row.readme ? (
            <ReadmeView markdown={row.readme} />
          ) : (
            <Typography typography="s2" color="text.secondary">
              This submission has no README.
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
            {row.log || "The checks recorded no run log for this submission."}
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
    );
    body = (
      <>
        <ReviewHeader
          row={row}
          refText={ref}
          stalled={stalled}
          tier={history.tier}
          meEmail={me.data?.user?.email}
        />
        {reviewable ? (
          <DecisionForm
            key={row.id}
            submission={row}
            viewer={me.data?.user}
            approveDisabled={diff.isPending || diff.isError}
            diffError={diff.isError ? diff.error : null}
            diffLoading={diff.isFetching}
            onRetryDiff={() => diff.refetch()}
            onOpenFile={openFile}
            onDecided={() =>
              navigate(hubPaths.review, {
                replace: true,
                state: { decided: ref },
              })
            }
            layout={({ flags, confirm, side, bar }) => (
              <ReviewGrid
                summary={summary}
                flags={flags}
                confirm={confirm}
                details={details}
                side={
                  <>
                    {side}
                    {publisher}
                  </>
                }
                bar={bar}
              />
            )}
          />
        ) : (
          <ReviewGrid
            summary={summary}
            flags={
              row.report && (
                <FlagGroups
                  groups={groups}
                  onOpenFile={openFile}
                  files={new Set((row.files || []).map((file) => file.path))}
                />
              )
            }
            side={
              <>
                <HubPanel title="Decision">
                  <Typography
                    typography="s1"
                    color="text.secondary"
                    data-testid="hub-review-status-text"
                  >
                    {stalled ? noCheckerStatus(audience) : row.statusText}
                  </Typography>
                </HubPanel>
                {publisher}
              </>
            }
            details={details}
          />
        )}
      </>
    );
  }

  return (
    <MaintainerGate
      title={row?.manifest?.title || "Review"}
      documentTitle={ref}
      description={
        row?.status !== "in_review"
          ? undefined
          : row.flags?.length
            ? "Answer each flag group, confirm the disclosure and the checklist, then decide."
            : "Confirm the disclosure and the checklist, then decide."
      }
      back={back}
      testId="hub-review"
    >
      {body}
    </MaintainerGate>
  );
}
