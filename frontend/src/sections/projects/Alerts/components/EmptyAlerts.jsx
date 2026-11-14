import { Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import { handleOnDocsClicked } from "../../../../utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const STEPS = [
  {
    title: "Pick a metric",
    description:
      "A system metric like latency, or an eval like hallucination rate.",
  },
  {
    title: "Set the condition",
    description: "Above or below a threshold, over the window you choose.",
  },
  {
    title: "Choose who's notified",
    description: "Add emails; every trigger is kept in the alert's log.",
  },
];

export default function EmptyAlerts({ onStartCreatingAlerts, mainPage }) {
  const { role } = useAuthContext();
  return (
    <EmptyState
      icon="solar:bell-linear"
      title="No alerts yet"
      description="Get notified when a metric or eval crosses a threshold you set."
      steps={STEPS}
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          onClick={() => onStartCreatingAlerts()}
          disabled={
            !RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role]
          }
        >
          Create alert
        </Button>
      }
      docsHref="https://docs.futureagi.com/docs/observe/features/alerts"
      onDocsClick={() =>
        handleOnDocsClicked(
          mainPage ? "alerts_main_page" : "observe_tab_alerts",
        )
      }
    />
  );
}

EmptyAlerts.displayName = "EmptyAlerts";

EmptyAlerts.propTypes = {
  onStartCreatingAlerts: PropTypes.func,
  mainPage: PropTypes.bool,
};
