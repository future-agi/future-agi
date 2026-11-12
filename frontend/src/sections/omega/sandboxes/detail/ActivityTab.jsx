import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { useActivity } from "../api/queries";
import SandboxErrorState from "../components/SandboxErrorState";
import { LoadingRows } from "../components/SessionGate";
import StatePanel from "../components/StatePanel";
import {
  actorLabel,
  formatBytes,
  formatDateTime,
  formatMs,
  timeAgo,
  verbLabel,
} from "../lib/format";

const mono = { fontFamily: "monospace", fontSize: 12 };
// Outcomes that are a choice or a limit, not a failure, stay in the quiet colour.
const QUIET_OUTCOMES = new Set(["ok", "cancelled", "timed_out"]);

/** The detail line of one audit entry: the command, file path or exit, never content. */
function detailText(line) {
  const d = line.detail || {};
  const parts = [];
  if (typeof d.command === "string") parts.push(`$ ${d.command}`);
  if (typeof d.path === "string") parts.push(d.path);
  if (typeof d.from === "string" && typeof d.to === "string")
    parts.push(`${d.from} → ${d.to}`);
  if (d.recursive === true) parts.push("and everything in it");
  if (typeof d.bytes === "number") parts.push(formatBytes(d.bytes));
  if (typeof d.exitCode === "number" && d.exitCode >= 0)
    parts.push(`exit ${d.exitCode}`);
  if (typeof d.durationMs === "number") parts.push(formatMs(d.durationMs));
  return parts.join(" · ");
}

export default function ActivityTab({ sandbox }) {
  const activity = useActivity(sandbox.id);
  if (activity.isLoading) return <LoadingRows rows={4} />;
  if (activity.error)
    return (
      <SandboxErrorState
        compact
        error={activity.error}
        onRetry={() => activity.refetch()}
      />
    );
  const lines = [...(activity.data ?? [])].sort(
    (a, b) => Date.parse(b.at) - Date.parse(a.at),
  );
  if (lines.length === 0) {
    return (
      <StatePanel
        compact
        icon="activity"
        title="No activity yet"
        description={
          sandbox.readOnly
            ? "This sandbox belongs to an agent run; its steps are in the run itself."
            : "Commands, file writes, snapshots and lifecycle changes appear here."
        }
      />
    );
  }
  return (
    <Box
      component="ol"
      data-testid="activity-list"
      sx={{
        listStyle: "none",
        m: 0,
        p: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      {lines.map((line, index) => {
        const ok = line.outcome === "ok";
        const detail = detailText(line);
        return (
          <Box
            component="li"
            key={`${line.at}:${index}`}
            data-testid="activity-row"
            data-verb={line.verb}
            sx={{
              px: 2,
              py: 1.25,
              display: "grid",
              gridTemplateColumns: { xs: "1fr", sm: "140px 1fr auto" },
              gap: { xs: 0.25, sm: 2 },
              alignItems: "baseline",
              borderTop: index ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Typography
              typography="s2"
              color="text.secondary"
              title={formatDateTime(line.at)}
            >
              {timeAgo(line.at)}
            </Typography>
            <Box sx={{ minWidth: 0 }}>
              <Typography typography="s1" color="text.primary">
                {verbLabel(line.verb)}
              </Typography>
              {detail && (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ ...mono, overflowWrap: "anywhere" }}
                >
                  {detail}
                </Typography>
              )}
            </Box>
            <Typography
              typography="s2"
              sx={{
                color: QUIET_OUTCOMES.has(line.outcome)
                  ? "text.secondary"
                  : "error.main",
              }}
            >
              {ok
                ? actorLabel(line.actor)
                : `${line.outcome.replace(/_/g, " ")} · ${actorLabel(line.actor)}`}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}

ActivityTab.propTypes = { sandbox: PropTypes.object.isRequired };
