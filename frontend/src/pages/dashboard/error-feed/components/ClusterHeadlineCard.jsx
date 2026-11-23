import React from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { formatDistanceToNowStrict } from "date-fns";
import Iconify from "src/components/iconify";
import { useErrorFeedStore } from "../store";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";
import { rootCauseRowsOf } from "./issueEvidence";

/*
  Root cause: what the analyzer stored about the issue, read before anything
  is run. Likely cause, the suggested change, what would prove that change
  wrong, and how many traces it is seen in. A key-value panel; no actions
  inside it — Run Epoch lives in the page header.
*/

const ago = (iso) => {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  try {
    return `${formatDistanceToNowStrict(then)} ago`;
  } catch {
    return null;
  }
};

/* "58 traces from 52 users, first seen 3 days ago" */
const seenInOf = (issue) => {
  const traces = issue?.trace_count;
  if (traces == null) return null;
  const users = issue?.users_affected;
  const first = issue?.firstSeenHuman ?? ago(issue?.first_seen);
  return [
    `${traces.toLocaleString()} ${traces === 1 ? "trace" : "traces"}`,
    users
      ? ` from ${users.toLocaleString()} ${users === 1 ? "user" : "users"}`
      : "",
    first ? `, first seen ${first}` : "",
  ].join("");
};

function KeyValueRow({ label, children, last }) {
  const cell = {
    px: 1.5,
    py: 0.75,
    borderBottom: last ? 0 : "1px solid",
    borderColor: "divider",
  };
  return (
    <>
      <Typography
        component="dt"
        fontSize="12px"
        color="text.tertiary"
        sx={{ ...cell, lineHeight: "20px", whiteSpace: "nowrap" }}
      >
        {label}
      </Typography>
      <Typography
        component="dd"
        fontSize="13px"
        color="text.primary"
        sx={{ ...cell, m: 0, lineHeight: "20px", minWidth: 0 }}
      >
        {children}
      </Typography>
    </>
  );
}
KeyValueRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  last: PropTypes.bool,
};

export default function ClusterHeadlineCard({ error }) {
  const collapsed = useErrorFeedStore((s) => s.clusterAnalysisCollapsed);
  const toggleCollapsed = useErrorFeedStore(
    (s) => s.toggleClusterAnalysisCollapsed,
  );

  const rows = rootCauseRowsOf(error);
  const seenIn = seenInOf(error);
  const all = [
    ...rows,
    ...(seenIn ? [{ key: "seen", label: "Seen in", text: seenIn }] : []),
  ];
  const cause = rows.find((r) => r.key === "cause")?.text;

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* Header: title, and the cause in one line while folded. */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        {...clickable(toggleCollapsed, { expanded: !collapsed })}
        sx={{
          ...focusRing,
          minHeight: 36,
          pl: 1.5,
          pr: 1,
          cursor: "pointer",
          userSelect: "none",
          minWidth: 0,
          borderBottom: collapsed ? 0 : "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          fontSize="13px"
          fontWeight={500}
          color="text.primary"
          sx={{ flexShrink: 0 }}
        >
          Root cause
        </Typography>
        {collapsed && cause && (
          <Typography
            fontSize="12px"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {cause}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={16}
          sx={{
            color: "text.secondary",
            flexShrink: 0,
            transform: collapsed ? "rotate(-90deg)" : "none",
            transition: "transform 0.2s",
          }}
        />
      </Stack>

      <Collapse in={!collapsed} timeout={200} unmountOnExit>
        {all.length ? (
          <Box
            component="dl"
            sx={{
              m: 0,
              display: "grid",
              gridTemplateColumns: "minmax(120px, max-content) minmax(0, 1fr)",
            }}
          >
            {all.map((r, i) => (
              <KeyValueRow
                key={r.key}
                label={r.label}
                last={i === all.length - 1}
              >
                {r.text}
              </KeyValueRow>
            ))}
          </Box>
        ) : (
          <Typography
            fontSize="13px"
            color="text.secondary"
            sx={{ px: 1.5, py: 1 }}
          >
            {"The analyzer hasn't stored a cause for this issue yet."}
          </Typography>
        )}
      </Collapse>
    </Box>
  );
}

ClusterHeadlineCard.propTypes = {
  error: PropTypes.object.isRequired,
  /* Kept so the page can still pass them; the panel has no actions. */
  onOpenAnalyze: PropTypes.func,
  onStartAnalysis: PropTypes.func,
  onCreateLinear: PropTypes.func,
};
