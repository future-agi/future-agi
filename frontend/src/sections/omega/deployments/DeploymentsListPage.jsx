import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import { useNavigate } from "react-router-dom";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { projectWords } from "src/sections/omega/shell/switches";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import CodeButton from "../code/CodeButton";
import { deploymentListRequests } from "../code/deploymentRequests";
import { useCredentials, useDeploymentList } from "./api/queries";
import DeployIcon from "./components/DeployIcon";
import DeploymentErrorState from "./components/DeploymentErrorState";
import DeploymentPreviewNotice from "./components/DeploymentPreviewNotice";
import DeploymentsFrame from "./components/DeploymentsFrame";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import StatePanel from "./components/StatePanel";
import { newDeploymentPath, secretsPath } from "./lib/links";
import { errorWithoutData } from "./lib/queryState";
import DeploymentsTable from "./list/DeploymentsTable";

/** What Deployments can deploy, in the words the P23 switch decides. */
function useProjectWords() {
  return projectWords(useOmegaPart(OMEGA_PART.DEPLOY_LANGGRAPH_JS));
}

const description = (words) =>
  `${words.kind} served from this workspace, each at its own URL.`;

function NewButton({ variant = "contained" }) {
  const navigate = useNavigate();
  return (
    <Button
      variant={variant}
      color="primary"
      size="small"
      onClick={() => navigate(newDeploymentPath())}
      startIcon={<DeployIcon name="plus" size={16} />}
      data-testid="new-deployment"
    >
      New deployment
    </Button>
  );
}

NewButton.propTypes = { variant: PropTypes.string };

/** The workspace's secrets; offered once the vault answers, so it never opens on a dead end. */
function SecretsButton() {
  const navigate = useNavigate();
  const credentials = useCredentials();
  if (!credentials.data && !credentials.error) return null;
  return (
    <>
      {errorWithoutData(credentials) && (
        <DeploymentErrorState
          compact
          error={credentials.error}
          onRetry={() => credentials.refetch()}
        />
      )}
      {credentials.data && (
        <Button
          variant="outlined"
          color="inherit"
          size="small"
          onClick={() => navigate(secretsPath())}
          startIcon={<DeployIcon name="key" size={16} />}
          data-testid="open-secrets"
        >
          Secrets
        </Button>
      )}
    </>
  );
}

function ListBody() {
  const words = useProjectWords();
  const list = useDeploymentList();
  const deployments = list.data ?? [];

  let content;
  if (list.isLoading) content = <LoadingRows />;
  else if (errorWithoutData(list))
    content = (
      <DeploymentErrorState error={list.error} onRetry={() => list.refetch()} />
    );
  else if (deployments.length === 0)
    content = (
      <StatePanel
        testId="deployments-empty"
        title="No deployments yet"
        description={`Deploy ${words.one} (${words.folder}) and it gets a URL, an access token, and revisions you can roll back to.`}
        action={<NewButton />}
      />
    );
  else content = <DeploymentsTable deployments={deployments} />;

  return (
    <DeploymentsFrame
      testId="omega-tab-deployments"
      title="Deployments"
      description={description(words)}
      action={
        <>
          {list.data && (
            <CodeButton
              requests={deploymentListRequests()}
              subject="Deployments"
            />
          )}
          <SecretsButton />
          {(deployments.length > 0 || (list.error && list.data != null)) && (
            <NewButton />
          )}
        </>
      }
    >
      <DeploymentPreviewNotice />
      {content}
    </DeploymentsFrame>
  );
}

export default function DeploymentsListPage() {
  const words = useProjectWords();
  return (
    <SessionGate
      fallbackFrame={(node) => (
        <DeploymentsFrame
          testId="omega-tab-deployments"
          title="Deployments"
          description={description(words)}
        >
          <DeploymentPreviewNotice />
          {node}
        </DeploymentsFrame>
      )}
    >
      <ListBody />
    </SessionGate>
  );
}
