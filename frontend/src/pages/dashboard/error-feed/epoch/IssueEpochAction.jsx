import PropTypes from "prop-types";
import { Button } from "@mui/material";

import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { useIssueEpoch } from "src/api/errorFeed/epochFromIssue";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import RetryButton from "src/sections/simulate/environments/workspace/improvements/RetryButton";
import ShipButton from "src/sections/simulate/environments/workspace/improvements/ShipButton";

import { epochHref, primaryAction } from "./issueEpochView";

// The issue header's one main action for its epoch: Run Epoch, the drawer's next
// step, the epoch's page, or what the epoch page itself would do next (ship, retry).
export default function IssueEpochAction({
  clusterId,
  projectId,
  onOpenDrawer,
}) {
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  const { status, known } = useIssueEpoch({ clusterId, projectId });
  // Never offer Run Epoch over a job the page couldn't read, or with no project.
  if (!allowed || !known || !projectId) return null;
  const action = primaryAction(status);
  const fix = status?.epoch;
  if (action.kind === "ship") return <ShipButton fix={fix} />;
  if (action.kind === "retry") return <RetryButton fix={fix} />;
  if (action.kind === "view") {
    return (
      <Button
        size="small"
        variant="contained"
        color="primary"
        component={RouterLink}
        href={epochHref(fix)}
        endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
      >
        {action.label}
      </Button>
    );
  }
  return (
    <Button
      size="small"
      variant="contained"
      color="primary"
      startIcon={<Iconify icon="solar:magic-stick-3-linear" width={14} />}
      onClick={onOpenDrawer}
      data-testid="issue-run-epoch"
    >
      {action.label}
    </Button>
  );
}

IssueEpochAction.propTypes = {
  clusterId: PropTypes.string.isRequired,
  projectId: PropTypes.string,
  onOpenDrawer: PropTypes.func.isRequired,
};
