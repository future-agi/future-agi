import PropTypes from "prop-types";
import { Button } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { useNavigate } from "react-router-dom";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import { issueFixNav, useIssueFix } from "./issueFix";
import IssueStripRow from "./IssueStripRow";
import { epochNameOf } from "./issueFixHeader";

const ago = (iso) => {
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};

/**
 * An issue that came back after its fix was released. Says which change was
 * meant to fix it — the version, how it shipped, the epoch that proved it —
 * so the reopen is a lead, not just a status. The header's one primary
 * button is Fix again (a new epoch); this row keeps View fix, and the old
 * epoch stays as the record of what didn't hold.
 */
export default function ReopenedBanner({ issue }) {
  const navigate = useNavigate();
  const { status, state } = useIssueFix(issue);
  const improving = useAgentImprovementFlag();
  if (!improving || status?.key !== "reopened" || !status.change) return null;
  const c = status.change;
  return (
    <IssueStripRow
      badge={
        <StatusBadge
          status="reopened"
          role="warning"
          variant="badge"
          label="Reopened"
        />
      }
      sub={
        c.reopenedBy
          ? `The change that was meant to fix it: ${c.name}${c.via ? `, shipped as ${c.via}` : ""}. Run Epoch again starts a new one from production.`
          : `The change that was meant to fix it: ${c.name}${c.via ? `, shipped as ${c.via}` : ""}. Its scenarios passed, so production does something they don't cover.`
      }
      action={
        <Button
          size="small"
          variant="outlined"
          onClick={() => navigate(...issueFixNav(status, issue?.cluster_id))}
          sx={{ flexShrink: 0 }}
        >
          {`View ${epochNameOf(state, status) || "Epoch"}`}
        </Button>
      }
    >
      {c.reopenedBy
        ? `Reopened by ${c.reopenedBy} ${ago(c.seenAt)}. The change on agent ${c.version} had been released ${ago(c.releasedAt)}.`
        : `Seen again on agent ${c.version}, released ${ago(c.releasedAt)}.`}
    </IssueStripRow>
  );
}

ReopenedBanner.propTypes = {
  issue: PropTypes.object,
};
