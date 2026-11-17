import React, { useId } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Tab from "@mui/material/Tab";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { SegmentedTabs } from "src/components/tabs/tabs";
import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { useMySubmissions } from "./api/queries";
import { countIfAny, splitRef } from "./lib/format";
import {
  DEFAULT_VIEW,
  SUBMISSION_VIEWS,
  answeredByNewer,
  nextStep,
  submissionViews,
  viewOf,
} from "./lib/submissionViews";
import {
  noCheckerStatus,
  selfHostedAudience,
  stalledWithoutChecker,
  useUploadsChecked,
  useUploadsOffAudience,
} from "./lib/uploadsChecked";
import HubFrame, { hubPageQuery } from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import HubNav from "./components/HubNav";
import { HubEmpty, HubError, HubSection } from "./components/HubStates";
import { StatusChip, TimeAgo } from "./components/StatusTimeline";
import {
  UploadsSetup,
  uploadsUnavailableCopy,
} from "./components/UploadsUnavailable";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./components/tabRow";

function refOf(row) {
  const name = row.name || row.hints?.name;
  const version = row.version || row.hints?.version;
  if (!name) return `${row.publisher}/…`;
  return `${row.publisher}/${name}${version ? `@${version}` : ""}`;
}

function entryOf(row) {
  return `${row.publisher}/${row.name || row.hints?.name || "…"}`;
}

// The way to an upload: the Publish page. While this server runs no Hub
// checker, Publish takes no upload and the Publish page says why; given the
// id of the notice that says so here (`offId`), Publish stays that link and
// carries the notice's sentence as its description, as each row's Upload a
// new version does. It is outlined then, not the page's main button, as no
// upload can go through it; with uploads on it is the contained one.
function PublishButton({ offId }) {
  return (
    <Button
      component={RouterLink}
      href={hubPaths.publish}
      variant={offId ? "outlined" : "contained"}
      size="small"
      aria-describedby={offId}
    >
      Publish
    </Button>
  );
}

PublishButton.propTypes = { offId: PropTypes.string };

// A view's name and, once above zero, its count in quieter secondary text.
// Its own component, as the other tab labels with a count are: the tab itself
// keeps the theme's look.
function ViewLabel({ label, count }) {
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "baseline", gap: 0.75 }}
    >
      {label}{" "}
      {count && (
        <Box component="span" sx={{ color: "text.secondary", fontWeight: 400 }}>
          {count}
        </Box>
      )}
    </Box>
  );
}

ViewLabel.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.string,
};

// The view switch: the design system's segmented control, as the Browse sort
// is. A view's count shows once it is above zero, as the kind chips' do.
function ViewTabs({ value, counts, onChange }) {
  return (
    <SegmentedTabs
      value={value}
      onChange={(_, next) => onChange(next)}
      {...TAB_ROW_PROPS}
      aria-label="Show"
      data-testid="hub-submissions-views"
      sx={{ minWidth: 0, ...TAB_ROW_SCROLL_SX }}
    >
      {SUBMISSION_VIEWS.map((view) => (
        <Tab
          key={view.value}
          value={view.value}
          data-testid={`hub-submissions-view-${view.value}`}
          label={
            <ViewLabel
              label={view.label}
              count={countIfAny(counts[view.value])}
            />
          }
        />
      ))}
    </SegmentedTabs>
  );
}

ViewTabs.propTypes = {
  value: PropTypes.string.isRequired,
  counts: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

const uploadsAre = (count) =>
  count === 1 ? "1 upload is" : `${count} uploads are`;

// How many uploads wait on someone else. Those the checks hold while no Hub
// checker is alive (`held`) wait with the checks not running; the rest are
// in checks or review.
function waitingSentence(count, held) {
  const rest = count - held;
  return [
    held ? `${uploadsAre(held)} waiting; checks are not running.` : "",
    rest ? `${uploadsAre(rest)} in checks or review.` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

// A view with nothing in it, while other views have rows: what that means,
// and the next thing to do, which is a view with rows in it or Publish
// (described by the notice `offId` while uploads are off).
function ViewEmpty({ view, counts, held, offId, onShow }) {
  const show = (target, label) => (
    <Button size="small" variant="outlined" onClick={() => onShow(target)}>
      {label}
    </Button>
  );
  const needs = counts.needs ? show(DEFAULT_VIEW, "See what needs you") : null;
  const waiting = counts.waiting
    ? show("waiting", "See what is waiting")
    : null;
  if (view === "published") {
    return (
      <HubEmpty
        title="Nothing published"
        description="A submission shows here once a maintainer approves it."
        action={needs || waiting || <PublishButton offId={offId} />}
      />
    );
  }
  if (view === "waiting") {
    return (
      <HubEmpty
        title="Nothing is waiting"
        description="No upload is in checks or review."
        action={needs || <PublishButton offId={offId} />}
      />
    );
  }
  return (
    <HubEmpty
      title="Nothing needs you right now"
      description={
        counts.waiting
          ? `${waitingSentence(counts.waiting, held)} An upload shows here if its checks fail or a maintainer asks for changes.`
          : "No upload is in checks or review, and no maintainer has asked you for changes."
      }
      action={waiting || <PublishButton offId={offId} />}
    />
  );
}

ViewEmpty.propTypes = {
  view: PropTypes.string.isRequired,
  counts: PropTypes.object.isRequired,
  held: PropTypes.number,
  offId: PropTypes.string,
  onShow: PropTypes.func.isRequired,
};

const LIST_SX = {
  m: 0,
  p: 0,
  listStyle: "none",
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  bgcolor: "background.paper",
  overflow: "hidden",
};

// One row on a wide page: the reference over its status line, the status,
// who moves it next, when it last moved, and the way in. A narrower page
// puts who moves it next under the reference; a phone also puts the status
// and the time on a line of their own.
const ROW_SX = {
  position: "relative",
  display: "grid",
  alignItems: "center",
  columnGap: 1.5,
  rowGap: 0.75,
  px: 2,
  py: 1.5,
  borderColor: "divider",
  gridTemplateColumns: "minmax(0, 1fr) max-content",
  gridTemplateAreas: '"main main" "status time" "next next"',
  "&:hover": { bgcolor: "action.hover" },
  [hubPageQuery("(min-width: 560px)")]: {
    gridTemplateColumns: "minmax(0, 1fr) max-content 88px 16px",
    gridTemplateAreas: '"main status time chevron" "next next next next"',
  },
  [hubPageQuery("(min-width: 880px)")]: {
    gridTemplateColumns: "minmax(0, 1fr) 152px 200px 88px 16px",
    gridTemplateAreas: '"main status next time chevron"',
  },
};

// The reference is the row's link. Its ::after covers the whole row, so a
// click anywhere on the row opens the submission, and its focus ring goes
// round the row. What else in the row takes a click or focus (the next
// step's link, the time) sits above it.
const REF_SX = {
  typography: "s1",
  fontWeight: 600,
  fontFamily: "monospace",
  color: "text.primary",
  overflowWrap: "anywhere",
  "&::after": { content: '""', position: "absolute", inset: 0 },
  "&:focus-visible": { outline: "none" },
  "&:focus-visible::after": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: -2,
  },
};

const NEXT_SX = {
  gridArea: "next",
  minWidth: 0,
  display: "flex",
  flexWrap: "wrap",
  alignItems: "baseline",
  columnGap: 1,
  rowGap: 0.25,
  [hubPageQuery("(min-width: 880px)")]: {
    flexDirection: "column",
    alignItems: "flex-start",
  },
};

const CHEVRON_SX = {
  gridArea: "chevron",
  color: "text.disabled",
  display: "none",
  [hubPageQuery("(min-width: 560px)")]: { display: "flex" },
};

// The way to do the next step, when there is one here: a new upload when it
// is the publisher's turn, the entry once it is published.
function nextAction(row, next) {
  if (next.who === "you") {
    return {
      href: hubPaths.publish,
      text: "Upload a new version",
      name: `Upload a new version of ${entryOf(row)}`,
    };
  }
  if (row.status !== "approved" || !row.published) return null;
  const published = splitRef(row.published);
  if (!published.name) return null;
  return {
    href: hubPaths.entry(published.publisher, published.name),
    text: "Open the entry",
    name: `Open ${published.publisher}/${published.name} on the Hub`,
  };
}

// Who moves this submission next ("Your turn", "Waiting on a maintainer")
// and the way to do it, in the app's link colour and underlined: the
// theme's primary colour is the body text colour in dark. While this server
// runs no Hub checker, Publish takes no upload: the page says so once, above
// the list (UploadsOffNotice), and Upload a new version carries that notice's
// sentence (`offId`, its id) as its description. A submission with no next
// step to name (NEXT_STEP.stalled) shows none.
function NextStep({ row, next, offId }) {
  if (!next.label) return null;
  const action = nextAction(row, next);
  const yours = next.who === "you";
  const off = Boolean(offId) && yours;
  return (
    <Box data-testid="hub-submission-next" sx={NEXT_SX}>
      <Typography
        component="span"
        typography="s2"
        fontWeight={yours ? 600 : 400}
        color={yours ? "text.primary" : "text.secondary"}
      >
        {next.label}
      </Typography>
      {action && (
        <Link
          component={RouterLink}
          href={action.href}
          aria-label={action.name}
          aria-describedby={off ? offId : undefined}
          color="text.link"
          underline="always"
          sx={{ position: "relative", zIndex: 1, typography: "s2" }}
        >
          {action.text}
        </Link>
      )}
    </Box>
  );
}

NextStep.propTypes = {
  row: PropTypes.object.isRequired,
  next: PropTypes.object.isRequired,
  offId: PropTypes.string,
};

// The one notice that uploads are off, above the list, while this server
// runs no Hub checker, in the words the Publish page gives this person
// (`copy`): its sentence under `id`, so each row's Upload a new version and
// an empty list's or view's Publish can carry it, and, for an owner or admin
// of a self-hosted server, the setup steps after it in the same notice.
function UploadsOffNotice({ id, copy }) {
  const sentence = (
    <Alert
      severity="info"
      role="status"
      data-testid="hub-submissions-uploads-off"
    >
      <Box component="span" id={id}>
        {copy.title}
      </Box>
    </Alert>
  );
  if (!copy.steps) return sentence;
  return (
    <HubSection testId="hub-submissions-uploads-setup">
      {sentence}
      <UploadsSetup copy={copy} headingComponent="h2" />
    </HubSection>
  );
}

UploadsOffNotice.propTypes = {
  id: PropTypes.string.isRequired,
  copy: PropTypes.object.isRequired,
};

// One row per submission: its reference (the link), where it is (or, with
// no Hub checker, why it waits, in the words for `audience`), its status,
// who moves it next, and when it last moved, exact to the minute with its
// time zone on hover and focus. The divider's colour comes after the
// borderTop shorthand, which resets it to the text colour.
function SubmissionRow({ row, first, stalled, answered, audience, offId }) {
  const next = nextStep(row, {
    stalled,
    answered,
    selfHosted: selfHostedAudience(audience),
  });
  return (
    <Box
      component="li"
      data-testid="hub-submission-row"
      data-status={row.status}
      data-next={next.who}
      sx={{
        ...ROW_SX,
        borderTop: first ? 0 : "1px solid",
        borderTopColor: "divider",
      }}
    >
      <Box sx={{ gridArea: "main", minWidth: 0 }}>
        <Link
          component={RouterLink}
          href={hubPaths.submission(row.id)}
          underline="hover"
          sx={REF_SX}
        >
          {refOf(row)}
        </Link>
        <Typography typography="s2" color="text.secondary">
          {stalled ? noCheckerStatus(audience) : row.statusText}
        </Typography>
      </Box>
      <Box sx={{ gridArea: "status", justifySelf: "start", maxWidth: "100%" }}>
        <StatusChip
          status={row.status}
          stalled={stalled || Boolean(row.checksStopped)}
        />
      </Box>
      <NextStep row={row} next={next} offId={offId} />
      <Typography
        component="div"
        typography="s3"
        color="text.tertiary"
        sx={{ gridArea: "time", justifySelf: "end", textAlign: "right" }}
      >
        <TimeAgo iso={row.updatedAt || row.createdAt} label="Updated" />
      </Typography>
      <Box aria-hidden="true" sx={CHEVRON_SX}>
        <HubIcon name="chevronRight" size={16} />
      </Box>
    </Box>
  );
}

SubmissionRow.propTypes = {
  row: PropTypes.object.isRequired,
  first: PropTypes.bool,
  stalled: PropTypes.bool,
  answered: PropTypes.bool,
  audience: PropTypes.string,
  offId: PropTypes.string,
};

function SubmissionList({ rows, view, stalled, answered, audience, offId }) {
  return (
    <Box
      component="ul"
      data-testid="hub-submissions-list"
      data-view={view}
      sx={LIST_SX}
    >
      {rows.map((row, index) => (
        <SubmissionRow
          key={row.id}
          row={row}
          first={index === 0}
          stalled={stalled(row)}
          answered={answered(row)}
          audience={audience}
          offId={offId}
        />
      ))}
    </Box>
  );
}

SubmissionList.propTypes = {
  rows: PropTypes.array.isRequired,
  view: PropTypes.string.isRequired,
  stalled: PropTypes.func.isRequired,
  answered: PropTypes.func.isRequired,
  audience: PropTypes.string,
  offId: PropTypes.string,
};

// While the list loads: the view switch and a few rows in the list's own
// frame, so nothing moves when the rows arrive.
function ListLoading() {
  return (
    <Box
      data-testid="hub-loading"
      aria-busy="true"
      sx={{ display: "flex", flexDirection: "column", gap: 2 }}
    >
      <Skeleton
        variant="rounded"
        width={360}
        height={32}
        sx={{ maxWidth: "100%" }}
      />
      <Box sx={LIST_SX}>
        {[0, 1, 2, 3].map((index) => (
          <Box
            key={index}
            data-testid="hub-submission-row-loading"
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              px: 2,
              py: 1.5,
              borderTop: index ? "1px solid" : 0,
              borderColor: "divider",
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Skeleton variant="text" width="40%" />
              <Skeleton variant="text" width="65%" />
            </Box>
            <Skeleton variant="rounded" width={112} height={20} />
            <Skeleton variant="text" width={64} />
          </Box>
        ))}
      </Box>
    </Box>
  );
}

// My submissions (SPEC §7.1) with live status: the list refreshes while any
// row is being checked or reviewed. While this server runs no Hub checker,
// nothing claims a check job, so a received, checking or check_error row
// cannot move: it says why (lib/uploadsChecked.js), its chip never says
// "Checks running", it waits on an administrator on a self-hosted server (on
// Cloud, and while the server's mode is not known, it names no next step), and
// it is not polled. One notice above the list says uploads are off, in place of
// a line on every row whose turn it is, in the Publish page's words for this
// person: the setup steps for an owner or admin of a self-hosted server.
// Every Publish stays a link to the Publish page, which says why: the tab
// row's as on every Hub page, and an empty list's or view's, outlined, with
// the notice's sentence as its description. With nothing submitted yet, the
// list's line says what the page will show, not how to upload. That line
// waits for the answer on uploads, so a server with no checker never shows,
// not even for one render, how to upload; the rest of the empty list does
// not wait.
//
// It opens on what needs the person: every failed or change-requested
// upload no newer upload has answered (lib/submissionViews.js). Waiting
// holds every upload in checks or review; Published and All sit beside
// them, and All keeps every submission. The view is URL state
// (?view=waiting, ?view=published, ?view=all), so a link opens it and Back
// returns to it.
export default function HubSubmissionsPage() {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  // Read the way the Publish page reads it (OMEGA_PART HUB_PUBLISH).
  const uploads = useUploadsChecked();
  const uploadsOff = uploads === "unchecked";
  // Who reads which words while uploads are off (lib/uploadsChecked.js).
  const audience = useUploadsOffAudience();
  const offCopy = uploadsUnavailableCopy(audience);
  const offId = useId();
  const submissions = useMySubmissions(undefined, { live: !uploadsOff });
  const [search, setSearch] = useSearchParams();
  const view = viewOf(search.get("view"));
  const setView = (next) => {
    const params = new URLSearchParams(search);
    if (next === DEFAULT_VIEW) params.delete("view");
    else params.set("view", next);
    setSearch(params, { replace: true });
  };
  const stalled = (row) => stalledWithoutChecker(uploads, row.status);
  const all = submissions.data || [];
  const views = submissionViews(all);
  const answered = answeredByNewer(all);
  const rows = views[view];
  const counts = {
    needs: views.needs.length,
    waiting: views.waiting.length,
    published: views.published.length,
    all: all.length,
  };
  // Waiting uploads the checks hold while no Hub checker is alive: this
  // server runs none, or the server's one reading for this list found none
  // (a row's checksStopped). The same rows whose chip says so.
  const held = views.waiting.filter(
    (row) => stalled(row) || row.checksStopped === true,
  ).length;
  return (
    <HubFrame
      title="My submissions"
      documentTitle="My submissions"
      description="What you or your publishers uploaded, and where each one is in checks and review."
      testId="hub-submissions"
      nav={<HubNav />}
    >
      {uploadsOff && <UploadsOffNotice id={offId} copy={offCopy} />}
      {submissions.isLoading ? (
        <ListLoading />
      ) : submissions.isError ? (
        <HubError
          title="Your submissions did not load"
          error={submissions.error}
          onRetry={() => submissions.refetch()}
        />
      ) : all.length === 0 ? (
        <HubEmpty
          title="Nothing submitted yet"
          description={
            uploads === "loading"
              ? undefined
              : uploadsOff
                ? "You will follow your uploads here through checks and review."
                : `${cliReleased ? "Publish from your terminal or upload a folder" : "Upload a folder"}. You will follow it here through checks and review.`
          }
          action={<PublishButton offId={uploadsOff ? offId : undefined} />}
        />
      ) : (
        <>
          <ViewTabs value={view} counts={counts} onChange={setView} />
          {rows.length === 0 ? (
            <ViewEmpty
              view={view}
              counts={counts}
              held={held}
              offId={uploadsOff ? offId : undefined}
              onShow={setView}
            />
          ) : (
            <SubmissionList
              rows={rows}
              view={view}
              stalled={stalled}
              answered={answered}
              audience={audience}
              offId={uploadsOff ? offId : undefined}
            />
          )}
        </>
      )}
    </HubFrame>
  );
}
