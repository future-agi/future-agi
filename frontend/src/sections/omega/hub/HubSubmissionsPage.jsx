import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CardActionArea from "@mui/material/CardActionArea";
import Tab from "@mui/material/Tab";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { SegmentedTabs } from "src/components/tabs/tabs";
import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { useMySubmissions } from "./api/queries";
import { countIfAny, timeAgo } from "./lib/format";
import {
  DEFAULT_VIEW,
  SUBMISSION_VIEWS,
  submissionViews,
  viewOf,
} from "./lib/submissionViews";
import {
  NO_CHECKER_STATUS,
  stalledWithoutChecker,
  useUploadsChecked,
} from "./lib/uploadsChecked";
import HubFrame from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import HubNav from "./components/HubNav";
import { HubEmpty, HubError, HubSkeletonCards } from "./components/HubStates";
import { StatusChip } from "./components/StatusTimeline";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./components/tabRow";

function refOf(row) {
  const name = row.name || row.hints?.name;
  const version = row.version || row.hints?.version;
  if (!name) return `${row.publisher}/…`;
  return `${row.publisher}/${name}${version ? `@${version}` : ""}`;
}

function PublishButton() {
  return (
    <Button
      component={RouterLink}
      href={hubPaths.publish}
      variant="contained"
      size="small"
    >
      Publish
    </Button>
  );
}

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

// A view with nothing in it, while other views have rows: what that means,
// and the next thing to do.
function ViewEmpty({ view, needsCount, onShowNeeds }) {
  if (view === "published") {
    return (
      <HubEmpty
        title="Nothing published"
        description="A submission shows here once a maintainer approves it."
        action={
          needsCount ? (
            <Button size="small" variant="outlined" onClick={onShowNeeds}>
              See what needs you
            </Button>
          ) : (
            <PublishButton />
          )
        }
      />
    );
  }
  return (
    <HubEmpty
      title="Nothing needs you right now"
      description="No upload is in checks or review, and no maintainer has asked you for changes."
      action={<PublishButton />}
    />
  );
}

ViewEmpty.propTypes = {
  view: PropTypes.string.isRequired,
  needsCount: PropTypes.number.isRequired,
  onShowNeeds: PropTypes.func.isRequired,
};

// One row per submission: its reference, where it is (or, with no Hub
// checker, why it waits), its status and when it last moved.
function SubmissionList({ rows, view, stalled }) {
  return (
    <Box
      component="ul"
      data-testid="hub-submissions-list"
      data-view={view}
      sx={{
        m: 0,
        p: 0,
        listStyle: "none",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      {rows.map((row, index) => (
        <Box
          component="li"
          key={row.id}
          sx={{
            borderTop: index ? "1px solid" : 0,
            borderColor: "divider",
          }}
        >
          <CardActionArea
            component={RouterLink}
            href={hubPaths.submission(row.id)}
            data-testid="hub-submission-row"
            data-status={row.status}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              px: 2,
              py: 1.5,
              flexWrap: { xs: "wrap", sm: "nowrap" },
            }}
          >
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography
                typography="s1"
                fontWeight={600}
                sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
              >
                {refOf(row)}
              </Typography>
              <Typography typography="s2" color="text.secondary">
                {stalled(row) ? NO_CHECKER_STATUS : row.statusText}
              </Typography>
            </Box>
            <StatusChip status={row.status} stalled={stalled(row)} />
            <Typography
              typography="s3"
              color="text.tertiary"
              sx={{ width: 90, textAlign: "right" }}
            >
              {timeAgo(row.updatedAt || row.createdAt)}
            </Typography>
            <Box
              sx={{
                color: "text.disabled",
                display: { xs: "none", sm: "flex" },
              }}
            >
              <HubIcon name="chevronRight" size={16} />
            </Box>
          </CardActionArea>
        </Box>
      ))}
    </Box>
  );
}

SubmissionList.propTypes = {
  rows: PropTypes.array.isRequired,
  view: PropTypes.string.isRequired,
  stalled: PropTypes.func.isRequired,
};

// My submissions (SPEC §7.1) with live status: the list refreshes while any
// row is being checked or reviewed. While this server runs no Hub checker,
// nothing claims a check job, so a received, checking or check_error row
// cannot move: it says why (lib/uploadsChecked.js), its chip never says
// "Checks running", and it is not polled.
//
// It opens on what needs the person: every upload still in checks or review,
// and every failed or change-requested one no newer upload has answered
// (lib/submissionViews.js). "Published" and "All" sit beside it; All keeps
// every submission. The view is URL state (?view=published, ?view=all), so a
// link opens it and Back returns to it.
export default function HubSubmissionsPage() {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  // Read the way the Publish page reads it (OMEGA_PART HUB_PUBLISH).
  const uploads = useUploadsChecked();
  const submissions = useMySubmissions(undefined, {
    live: uploads !== "unchecked",
  });
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
  const rows = views[view];
  const counts = {
    needs: views.needs.length,
    published: views.published.length,
    all: all.length,
  };
  return (
    <HubFrame
      title="My submissions"
      documentTitle="My submissions"
      description="What you or your publishers uploaded, and where each one is in checks and review."
      testId="hub-submissions"
    >
      <HubNav />
      {submissions.isLoading ? (
        <HubSkeletonCards count={3} />
      ) : submissions.isError ? (
        <HubError
          error={submissions.error}
          onRetry={() => submissions.refetch()}
        />
      ) : all.length === 0 ? (
        <HubEmpty
          title="Nothing submitted yet"
          description={`${cliReleased ? "Publish from your terminal or upload a folder" : "Upload a folder"}. You will follow it here through checks and review.`}
          action={<PublishButton />}
        />
      ) : (
        <>
          <ViewTabs value={view} counts={counts} onChange={setView} />
          {rows.length === 0 ? (
            <ViewEmpty
              view={view}
              needsCount={counts.needs}
              onShowNeeds={() => setView(DEFAULT_VIEW)}
            />
          ) : (
            <SubmissionList rows={rows} view={view} stalled={stalled} />
          )}
        </>
      )}
    </HubFrame>
  );
}
