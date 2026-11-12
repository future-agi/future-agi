import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Avatar,
  Box,
  ButtonBase,
  Collapse,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { fmtCount } from "src/sections/common/format/fmt";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";

const TYPE_ICON = {
  created: "mdi:plus",
  deleted: "solar:trash-bin-trash-linear",
  restored: "mdi:restore",
  run: "material-symbols:science-outline",
  queue: "mdi:tray-arrow-down",
  splits: "mdi:tag-outline",
  runs: "material-symbols:science-outline",
  comment: "mdi:comment-outline",
};

const initial = (name) =>
  String(name || "?")
    .trim()
    .charAt(0)
    .toUpperCase() || "?";

const sourceText = (source) => {
  if (!source) return "Created";
  if (source.type === "upload")
    return source.file_name
      ? `Created from upload ${source.file_name}`
      : "Imported from a file";
  if (source.type === "trace")
    return `Created from trace${source.project ? ` · ${source.project}` : ""}`;
  if (source.type === "experiment")
    return `Added from ${source.run_label ?? "an experiment"}`;
  return `Created from ${source.type}`;
};

const B = ({ children }) => (
  <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
    {children}
  </Box>
);

B.propTypes = { children: PropTypes.node };

// A cell event's `action` (row history): what happened to the value.
const CELL_VERB = {
  create: "set",
  edit: "changed",
  delete: "removed",
  restore: "restored",
};

/** One-line description of an N8 activity event. */
function describeEvent(ev, { onOpenRun, onOpenComment } = {}) {
  switch (ev.type) {
    // W4-1: one comment thread on the row.
    case "comment": {
      const where = [
        ev.run_label,
        ev.anchor?.kind === "field" ? ev.anchor.column_name : null,
        ev.anchor?.kind === "step" ? `step ${ev.anchor.step_label}` : null,
      ].filter(Boolean);
      // A deleted root that still has replies keeps its thread.
      const text = ev.deleted ? "Comment deleted" : `“${ev.excerpt ?? ""}”`;
      return (
        <>
          {ev.deleted ? "A thread" : <B>{ev.actor ?? "Someone"}</B>}
          {ev.deleted ? "" : " commented"}
          {where.length ? ` on ${where.join(" · ")}` : ""}
          {ev.deleted ? ": " : " "}
          {onOpenComment ? (
            <ButtonBase
              onClick={() => onOpenComment(ev)}
              data-testid="activity-comment-link"
              sx={{
                typography: "inherit",
                color: "primary.main",
                verticalAlign: "baseline",
                borderRadius: 0.5,
                textAlign: "left",
                "&:hover": { textDecoration: "underline" },
              }}
            >
              {text}
            </ButtonBase>
          ) : (
            text
          )}
          {ev.replies ? ` · ${fmtCount(ev.replies, "reply", "replies")}` : ""}
          {ev.resolved ? (
            <Box component="span" sx={{ color: "text.disabled" }}>
              {" · resolved"}
            </Box>
          ) : null}
        </>
      );
    }
    case "edit": {
      const verb = CELL_VERB[ev.action] ?? "changed";
      return (
        <>
          <B>{ev.actor ?? "Someone"}</B> {verb} {ev.column ?? "a field"}
        </>
      );
    }
    case "splits": {
      const parts = [
        ev.added?.length ? `added to ${ev.added.join(", ")}` : null,
        ev.removed?.length ? `removed from ${ev.removed.join(", ")}` : null,
      ].filter(Boolean);
      return (
        <>
          <B>{ev.actor}</B> {parts.join(" and ") || "changed splits"}
        </>
      );
    }
    case "created":
      return (
        <>
          {sourceText(ev.source)}
          {ev.actor ? (
            <>
              {" "}
              by <B>{ev.actor}</B>
            </>
          ) : null}
        </>
      );
    case "deleted":
      return (
        <>
          <B>{ev.actor ?? "Someone"}</B> deleted this row
        </>
      );
    case "restored":
      return (
        <>
          <B>{ev.actor ?? "Someone"}</B> restored this row
        </>
      );
    case "queue":
      return (
        <>
          <B>{ev.actor}</B> added to queue “{ev.queue}”
        </>
      );
    case "score": {
      const v = ev.value;
      const thumb = v === "up" || v === "down";
      return (
        <>
          <B>{ev.actor}</B> scored {ev.label}{" "}
          {thumb ? (
            <Iconify
              icon={
                v === "up" ? "octicon:thumbsup-24" : "octicon:thumbsdown-24"
              }
              width={13}
              sx={{
                verticalAlign: "-2px",
                color: v === "up" ? "success.main" : "error.main",
              }}
            />
          ) : (
            <B>{String(v)}</B>
          )}
        </>
      );
    }
    case "run":
      return (
        <>
          Ran in{" "}
          {onOpenRun && ev.run_id ? (
            <Tooltip title="Open this result in the experiment" placement="top">
              <ButtonBase
                onClick={() => onOpenRun(ev)}
                sx={{
                  typography: "inherit",
                  fontWeight: 600,
                  color: "primary.main",
                  verticalAlign: "baseline",
                  borderRadius: 0.5,
                  "&:hover": { textDecoration: "underline" },
                }}
              >
                {ev.run_label}
              </ButtonBase>
            </Tooltip>
          ) : (
            <B>{ev.run_label}</B>
          )}
          {ev.result ? (
            <Box
              component="span"
              sx={{
                color:
                  ev.passed === true
                    ? "green.600"
                    : ev.passed === false
                      ? "red.600"
                      : "inherit",
              }}
            >
              {` · ${ev.result}`}
            </Box>
          ) : (
            ""
          )}
        </>
      );
    case "runs":
      // R3 (RV#22): a burst of runs folded into one line.
      return (
        <>
          Ran in <B>{fmtCount(ev.runs?.length ?? 0, "run")}</B>
          {ev.passed ? (
            <Box component="span" sx={{ color: "green.600" }}>
              {` · ${fmtCount(ev.passed)} passed`}
            </Box>
          ) : null}
          {ev.failed ? (
            <Box component="span" sx={{ color: "red.600" }}>
              {` · ${fmtCount(ev.failed)} failed`}
            </Box>
          ) : null}
        </>
      );
    default:
      return <>{ev.text ?? ev.type}</>;
  }
}

function ActivityRow({ ev, onOpenVersion, onOpenRun, onOpenComment }) {
  const [open, setOpen] = useState(false);
  const isBurst = ev.type === "runs" && Array.isArray(ev.runs);
  const expandable =
    (ev.type === "edit" && (ev.before != null || ev.after != null)) || isBurst;
  // A set, removed or restored cell shows its value; an edit its diff.
  const valueOnly = ["create", "delete", "restore"].includes(ev.action);
  const shortened = Boolean(ev.before_truncated || ev.after_truncated);
  const person =
    [
      "edit",
      "splits",
      "queue",
      "score",
      "deleted",
      "restored",
      "comment",
    ].includes(ev.type) && ev.actor;
  return (
    <Box sx={{ py: 0.75 }}>
      <Stack direction="row" alignItems="flex-start" gap={1}>
        <Avatar
          sx={{
            width: 20,
            height: 20,
            fontSize: 10,
            fontWeight: 700,
            mt: 0.125,
            bgcolor: person ? "primary.lighter" : "action.hover",
            color: person ? "primary.darker" : "text.secondary",
          }}
        >
          {person ? (
            initial(ev.actor)
          ) : (
            <Iconify
              icon={TYPE_ICON[ev.type] ?? "mdi:circle-small"}
              width={12}
            />
          )}
        </Avatar>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ minWidth: 0 }}
          >
            {ev.version != null &&
              (onOpenVersion ? (
                <Tooltip
                  title={`View the row as of v${ev.version}`}
                  placement="top"
                >
                  <ButtonBase
                    onClick={() => onOpenVersion(ev.version)}
                    aria-label={`View v${ev.version}`}
                    sx={{ borderRadius: 0.5 }}
                  >
                    <VersionNumber number={ev.version} />
                  </ButtonBase>
                </Tooltip>
              ) : (
                <VersionNumber number={ev.version} />
              ))}
            <Typography
              typography="s1"
              color="text.secondary"
              sx={{ minWidth: 0 }}
            >
              {describeEvent(ev, { onOpenRun, onOpenComment })}
            </Typography>
          </Stack>
          {ev.type === "score" && ev.notes && (
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ mt: 0.25, fontStyle: "italic" }}
            >
              “{ev.notes}”
            </Typography>
          )}
          {expandable && (
            <ButtonBase
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              sx={{
                mt: 0.25,
                typography: "s2",
                color: "primary.main",
                borderRadius: 0.5,
              }}
            >
              {isBurst
                ? open
                  ? "Hide runs"
                  : "Show runs"
                : valueOnly
                  ? open
                    ? "Hide value"
                    : "Show value"
                  : open
                    ? "Hide diff"
                    : "Show diff"}
              <Iconify
                icon={open ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
                width={14}
              />
            </ButtonBase>
          )}
          {isBurst && (
            <Collapse in={open} unmountOnExit>
              <Stack gap={0.25} sx={{ mt: 0.5, pl: 0.25 }}>
                {ev.runs.map((r, i) => (
                  <Stack
                    key={`${r.run_id ?? i}`}
                    direction="row"
                    alignItems="baseline"
                    gap={1}
                  >
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      sx={{ flex: 1, minWidth: 0 }}
                    >
                      {describeEvent(r, { onOpenRun })}
                    </Typography>
                    {r.at && (
                      <RelativeTime
                        iso={r.at}
                        sx={{
                          typography: "s3",
                          color: "text.disabled",
                          flexShrink: 0,
                        }}
                      />
                    )}
                  </Stack>
                ))}
              </Stack>
            </Collapse>
          )}
          {expandable && !isBurst && (
            <Collapse in={open} unmountOnExit>
              <Box
                sx={{
                  mt: 0.75,
                  p: 1,
                  borderRadius: 0.5,
                  bgcolor: "background.neutral",
                }}
              >
                <TextDiff from={ev.before} to={ev.after} />
                {shortened && (
                  // The history keeps the start of long values only: an
                  // edit past that point leaves the two previews equal.
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    data-testid="activity-preview-note"
                    sx={{ mt: 0.75 }}
                  >
                    {ev.before === ev.after
                      ? "The change is past the part of the value shown here."
                      : "Long values are shortened here."}
                  </Typography>
                )}
              </Box>
            </Collapse>
          )}
        </Box>
        {ev.at && (
          // R3 (RV#22): one relative-time format, absolute (+ who) on hover.
          <RelativeTime
            iso={ev.at}
            by={person ? ev.actor : undefined}
            sx={{
              typography: "s2",
              color: "text.disabled",
              flexShrink: 0,
              whiteSpace: "nowrap",
            }}
          />
        )}
      </Stack>
    </Box>
  );
}

ActivityRow.propTypes = {
  ev: PropTypes.object.isRequired,
  onOpenVersion: PropTypes.func,
  onOpenRun: PropTypes.func,
  onOpenComment: PropTypes.func,
};

/**
 * ActivityList — a row's history (N8 events), in the row layout of
 * annotate/annotation-history.jsx: avatar, actor, one-line description,
 * relative time, optional expand (edit diffs). Working minimal version
 * (L0; owned by L1). Renders events in the order given (newest first).
 *
 * Props (frozen):
 * @param {Array<{type: "edit"|"splits"|"created"|"deleted"|"restored"|"queue"|"score"|"run",
 *   actor?: string, at?: string, version?: number}>} props.events
 * Row history events (dataset.rowExtras.activity) also carry `id`,
 * `action` (a cell's `create | edit | delete | restore`) and
 * `before_truncated` / `after_truncated` for shortened value previews.
 * Optional (L1): `onOpenVersion(number)` makes version tags links;
 * `onOpenRun(event)` makes a run event's label a link to the result.
 * R3 (P2, RV#22): a `{type: "runs", runs, passed, failed}` event renders as
 * `Ran in 9 runs · 6 passed` with `Show runs`; times use RelativeTime.
 */
const ActivityList = ({ events, onOpenVersion, onOpenRun, onOpenComment }) => {
  if (!events?.length)
    return (
      <Typography typography="s1" color="text.secondary" sx={{ py: 2 }}>
        No activity on this row yet.
      </Typography>
    );
  return (
    <Stack
      divider={<Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />}
    >
      {events.map((ev, i) => (
        <ActivityRow
          key={
            ev.id != null ? `id-${ev.id}` : `${ev.type}-${ev.at ?? ""}-${i}`
          }
          ev={ev}
          onOpenVersion={onOpenVersion}
          onOpenRun={onOpenRun}
          onOpenComment={onOpenComment}
        />
      ))}
    </Stack>
  );
};

ActivityList.propTypes = {
  events: PropTypes.arrayOf(PropTypes.object),
  onOpenVersion: PropTypes.func,
  onOpenRun: PropTypes.func,
  /** W4-1: a `comment` event's excerpt opens the thread. */
  onOpenComment: PropTypes.func,
};

export default ActivityList;
