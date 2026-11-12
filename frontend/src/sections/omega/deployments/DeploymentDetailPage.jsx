import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import { useNavigate, useParams } from "react-router-dom";

import CodeButton from "../code/CodeButton";
import { deploymentDetailRequests } from "../code/deploymentRequests";
import { useCanManageDeployments } from "../code/manageRole";
import { useDeployment } from "./api/queries";
import DeployIcon from "./components/DeployIcon";
import DeploymentErrorState from "./components/DeploymentErrorState";
import DeploymentsFrame from "./components/DeploymentsFrame";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import { QuietTag, StatusChip } from "./components/StatusChip";
import LogsTab from "./detail/LogsTab";
import OverviewTab from "./detail/OverviewTab";
import RevisionsTab from "./detail/RevisionsTab";
import SettingsTab from "./detail/SettingsTab";
import { isBusy } from "./lib/format";
import { isHostOnly } from "./lib/reach";
import {
  DEPLOYMENT_TABS,
  deploymentPath,
  deploymentsPath,
  newDeploymentPath,
} from "./lib/links";

const TAB_LABELS = {
  overview: "Overview",
  revisions: "Revisions",
  logs: "Logs",
  settings: "Settings",
};

const BACK = { href: deploymentsPath(), label: "Deployments" };

// Until the deployment's own name is known (loading, refused, not found) the
// title names the id the address asked for, as the shell's tab contract does.
const pendingTitle = (id) => `Deployment · ${id}`;

function DetailBody({ id, tab }) {
  const navigate = useNavigate();
  const query = useDeployment(id);
  const canManage = useCanManageDeployments();
  const deployment = query.data;
  const current = DEPLOYMENT_TABS.includes(tab) ? tab : "overview";

  if (query.isLoading || !deployment) {
    return (
      <DeploymentsFrame
        title={pendingTitle(id)}
        documentTitle="Deployment · Deployments"
        back={BACK}
        testId="omega-tab-deployment-detail"
      >
        {query.isLoading ? (
          <LoadingRows />
        ) : (
          <DeploymentErrorState
            error={query.error}
            onRetry={() => query.refetch()}
          />
        )}
      </DeploymentsFrame>
    );
  }

  const panels = {
    overview: <OverviewTab deployment={deployment} />,
    revisions: <RevisionsTab deployment={deployment} />,
    logs: <LogsTab deployment={deployment} />,
    settings: <SettingsTab deployment={deployment} />,
  };

  return (
    <DeploymentsFrame
      testId="omega-tab-deployment-detail"
      title={deployment.name}
      documentTitle={`${deployment.name} · Deployments`}
      back={BACK}
      meta={
        <>
          <StatusChip
            status={deployment.status}
            hostOnly={isHostOnly(deployment)}
          />
          {deployment.liveRevision ? (
            <QuietTag testId="live-revision">
              Revision {deployment.liveRevision}{" "}
              {isHostOnly(deployment) ? "running" : "live"}
            </QuietTag>
          ) : null}
          {isBusy(deployment.status) && deployment.operation ? (
            <Typography typography="s2" color="text.secondary">
              Revision {deployment.operation.revision} in progress
            </Typography>
          ) : null}
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ fontFamily: "monospace" }}
          >
            {deployment.id}
          </Typography>
        </>
      }
      action={
        <>
          <CodeButton
            requests={deploymentDetailRequests(deployment, current, {
              canManage,
            })}
            subject="this deployment"
          />
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={isBusy(deployment.status)}
            onClick={() => navigate(newDeploymentPath(deployment.id))}
            startIcon={<DeployIcon name="rocket" size={16} />}
            data-testid="deploy-new-revision"
          >
            Deploy new revision
          </Button>
        </>
      }
    >
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 2 }}>
        <Tabs
          value={current}
          onChange={(_, value) =>
            navigate(deploymentPath(deployment.id, value))
          }
          variant="scrollable"
          scrollButtons={false}
          aria-label="Deployment sections"
          sx={{
            minHeight: 40,
            "& .MuiTab-root": {
              minHeight: 40,
              px: 0,
              mr: { xs: 2.5, sm: 3 },
              typography: "s1",
              fontWeight: 500,
            },
          }}
        >
          {DEPLOYMENT_TABS.map((key) => (
            <Tab
              key={key}
              value={key}
              label={TAB_LABELS[key]}
              data-testid={`tab-${key}`}
              disableRipple
            />
          ))}
        </Tabs>
      </Box>
      <Box
        role="tabpanel"
        data-testid={`panel-${current}`}
        sx={{ minWidth: 0 }}
      >
        {panels[current]}
      </Box>
    </DeploymentsFrame>
  );
}

DetailBody.propTypes = {
  id: PropTypes.string.isRequired,
  tab: PropTypes.string,
};

export default function DeploymentDetailPage() {
  const { id, tab } = useParams();
  return (
    <SessionGate
      fallbackFrame={(node) => (
        <DeploymentsFrame
          title={pendingTitle(id)}
          documentTitle="Deployment · Deployments"
          back={BACK}
          testId="omega-tab-deployment-detail"
        >
          {node}
        </DeploymentsFrame>
      )}
    >
      <DetailBody key={id} id={id} tab={tab} />
    </SessionGate>
  );
}
