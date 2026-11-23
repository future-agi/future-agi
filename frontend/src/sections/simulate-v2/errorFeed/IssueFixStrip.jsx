import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Button, Link } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { paths } from "src/routes/paths";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import { improvementPath } from "../improvements/improvementNav";
import { useIssueFixHeader } from "./issueFixHeader";
import Row from "./IssueStripRow";

const linkSx = {
  typography: "inherit",
  fontWeight: 600,
  color: "text.primary",
  verticalAlign: "baseline",
};

/**
 * Under the issue header: what the fix action (an epoch on this issue) needs
 * before it can run, or how the issue's last epoch ended. The header's one
 * primary button is the way on (Connect agent, Link agent, Retry Epoch N,
 * Fix again); this row only says why, with View Epoch N where there is one.
 */
export default function IssueFixStrip({ issue }) {
  const navigate = useNavigate();
  const improving = useAgentImprovementFlag();
  const { link, prereq, version, ended, hold, agent } =
    useIssueFixHeader(issue);
  if (!improving) return null;
  const project = link.name || "this issue's project";

  const rows = [];

  if (prereq === "no-agent") {
    rows.push(
      <Row key="prereq" icon="solar:info-circle-linear">
        {`No agent is connected yet. Connect the agent whose traces land in ${project} to run an epoch on this issue.`}
      </Row>,
    );
  } else if (prereq === "unlinked") {
    rows.push(
      <Row key="prereq" icon="solar:info-circle-linear">
        {`${project} isn't linked to an agent. Link the agent whose traces land there to run an epoch on this issue.`}
      </Row>,
    );
  }

  if (hold) {
    const where =
      hold.action === "overview"
        ? "overview"
        : hold.action === "connections"
          ? "connections"
          : null;
    rows.push(
      <Row
        key="hold"
        badge={
          <StatusBadge
            status="blocked"
            role="warning"
            variant="badge"
            label="Can't run"
          />
        }
        sub={hold.body}
        action={
          where && agent ? <HoldAction tab={where} agentId={agent.id} /> : null
        }
      >
        {hold.title}
      </Row>,
    );
  }

  if (ended) {
    const n = ended.name;
    const envLink = ended.env ? (
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={() =>
          navigate(paths.dashboard.simulate.environmentDetail(ended.envId))
        }
        sx={linkSx}
      >
        {ended.env.name}
      </Link>
    ) : (
      "its environment"
    );
    const badgeLabel =
      ended.kind === "ended"
        ? `${n} · ${ended.label}`
        : `${n} · Stopped`;
    const text =
      ended.kind === "stopped" ? (
        <>
          {`${n} in `}
          {envLink}
          {
            " stopped: an issue occurred while running it. Your agent did not change, and this doesn't count against it."
          }
        </>
      ) : ended.kind === "blocked" ? (
        <>
          {`${n} in `}
          {envLink}
          {` couldn't run: ${ended.reason || "something only you can change"}. Your agent did not change.`}
        </>
      ) : ended.kind === "stopped-by" ? (
        <>
          {`${n} in `}
          {envLink}
          {
            " was stopped before it finished. Nothing it tried was checked or shipped, and your agent did not change."
          }
        </>
      ) : (
        <>
          {`${n} in `}
          {envLink}
          {` ended without a change to ship: ${ended.label.toLowerCase()}${ended.detail ? ` (${ended.detail})` : ""}. Your agent did not change.`}
        </>
      );
    const sub = [
      ended.spent &&
        `Spent ${ended.spent} before it ${ended.kind === "ended" ? "ended" : "stopped"}.`,
      ended.kind === "stopped" &&
        !ended.canRetry &&
        "It was already retried once — Run Epoch again starts a new one.",
    ]
      .filter(Boolean)
      .join(" ");
    const view = ended.optId
      ? () => navigate(improvementPath(ended.envId, ended.optId), {
          state: { fromIssue: issue?.cluster_id },
        })
      : ended.envId
        ? () =>
            navigate(
              `${paths.dashboard.simulate.environmentDetail(ended.envId)}/improvements`,
            )
        : null;
    rows.push(
      <Row
        key="ended"
        badge={
          <StatusBadge
            status="stopped"
            role="neutral"
            variant="badge"
            label={badgeLabel}
          />
        }
        sub={sub || null}
        action={
          view ? (
            <Button
              size="small"
              variant="outlined"
              onClick={view}
              sx={{ flexShrink: 0 }}
            >
              {`View ${n}`}
            </Button>
          ) : null
        }
      >
        {text}
      </Row>,
    );
  }

  if (version) {
    rows.push(
      <Row key="version" icon="solar:info-circle-linear">
        {`Traces are from ${version.traced}; your agent is now ${version.now}. The epoch starts from ${version.now}.`}
      </Row>,
    );
  }

  return rows.length ? <>{rows}</> : null;
}

IssueFixStrip.propTypes = { issue: PropTypes.object };

/* The way out of a hold: the agent's Connections tab or its Overview. */
function HoldAction({ tab, agentId }) {
  const navigate = useNavigate();
  return (
    <Button
      size="small"
      variant="outlined"
      onClick={() =>
        navigate(`${paths.dashboard.agents}/connected/${agentId}?tab=${tab}`)
      }
      sx={{ flexShrink: 0 }}
    >
      {tab === "overview" ? "Open the agent's Overview" : "Open Connections"}
    </Button>
  );
}
HoldAction.propTypes = { tab: PropTypes.string, agentId: PropTypes.string };
