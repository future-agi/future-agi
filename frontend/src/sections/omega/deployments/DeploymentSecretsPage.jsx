import React, { useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";

import CodeButton from "../code/CodeButton";
import { deploymentSecretsRequests } from "../code/deploymentRequests";
import { useCredentials } from "./api/queries";
import DeployIcon from "./components/DeployIcon";
import DeploymentErrorState from "./components/DeploymentErrorState";
import DeploymentsFrame from "./components/DeploymentsFrame";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import StatePanel from "./components/StatePanel";
import { deploymentsPath } from "./lib/links";
import { errorWithoutData } from "./lib/queryState";
import { activeNames } from "./lib/secretForm";
import AddSecretDialog from "./secrets/AddSecretDialog";
import DeleteSecretDialog from "./secrets/DeleteSecretDialog";
import ReplaceSecretDialog from "./secrets/ReplaceSecretDialog";
import SecretsTable from "./secrets/SecretsTable";
import { useCanManageSecrets } from "./secrets/useSecrets";

const TITLE = "Secrets";
const DESCRIPTION =
  "API keys and other values your deployments read as environment variables. Nobody can read a value back after it is saved.";
const BACK = { href: deploymentsPath(), label: "Deployments" };

function AddButton({ onClick, variant = "contained" }) {
  return (
    <Button
      variant={variant}
      color="primary"
      size="small"
      onClick={onClick}
      startIcon={<DeployIcon name="plus" size={16} />}
      data-testid="add-secret"
    >
      Add secret
    </Button>
  );
}

AddButton.propTypes = {
  onClick: PropTypes.func.isRequired,
  variant: PropTypes.string,
};

function SecretsBody() {
  const credentials = useCredentials();
  const canManage = useCanManageSecrets();
  const { enqueueSnackbar } = useSnackbar();
  const [adding, setAdding] = useState(false);
  const [replacing, setReplacing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const secrets = credentials.data ?? [];
  const openAdd = () => setAdding(true);

  let content;
  if (credentials.isLoading) content = <LoadingRows rows={3} />;
  else if (errorWithoutData(credentials))
    content = (
      <DeploymentErrorState
        error={credentials.error}
        notFoundTitle="Secrets not found"
        onRetry={() => credentials.refetch()}
      />
    );
  else if (secrets.length === 0)
    content = (
      <StatePanel
        testId="secrets-empty"
        icon="key"
        title="No secrets yet"
        description={
          canManage
            ? "Add an API key once, then pick it by name in any deployment. Nobody can read a value back after it is saved."
            : "Workspace owners and admins add secrets here. You can pick any secret by name when you deploy."
        }
        action={canManage ? <AddButton onClick={openAdd} /> : null}
      />
    );
  else
    content = (
      <SecretsTable
        secrets={secrets}
        canManage={canManage}
        onReplace={setReplacing}
        onDelete={setDeleting}
      />
    );

  return (
    <DeploymentsFrame
      testId="omega-deployment-secrets"
      title={TITLE}
      documentTitle="Secrets · Deployments"
      description={DESCRIPTION}
      back={BACK}
      action={
        <>
          {credentials.data && (
            <CodeButton
              requests={deploymentSecretsRequests()}
              subject="secrets"
            />
          )}
          {canManage &&
            credentials.data != null &&
            (secrets.length > 0 || credentials.error) && (
              <AddButton onClick={openAdd} />
            )}
        </>
      }
    >
      {!canManage && secrets.length > 0 && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ mb: 1.5 }}
          data-testid="secrets-role-note"
        >
          Workspace owners and admins add, replace and delete secrets.
        </Typography>
      )}
      {content}
      {canManage && (
        <>
          <AddSecretDialog
            open={adding}
            takenNames={activeNames(secrets)}
            onClose={() => setAdding(false)}
            onAdded={(record) =>
              enqueueSnackbar(`${record?.name ?? "Secret"} saved`, {
                variant: "success",
              })
            }
          />
          <ReplaceSecretDialog
            secret={replacing}
            onClose={(done) => {
              if (done)
                enqueueSnackbar(`${replacing.name}: new value saved`, {
                  variant: "success",
                });
              setReplacing(null);
            }}
          />
          <DeleteSecretDialog
            secret={deleting}
            onClose={(done) => {
              if (done)
                enqueueSnackbar(`${deleting.name} deleted`, {
                  variant: "success",
                });
              setDeleting(null);
            }}
          />
        </>
      )}
    </DeploymentsFrame>
  );
}

export default function DeploymentSecretsPage() {
  return (
    <SessionGate
      fallbackFrame={(node) => (
        <DeploymentsFrame
          testId="omega-deployment-secrets"
          title={TITLE}
          description={DESCRIPTION}
          back={BACK}
        >
          {node}
        </DeploymentsFrame>
      )}
    >
      <SecretsBody />
    </SessionGate>
  );
}
