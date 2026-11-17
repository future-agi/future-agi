import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { switchoverSentence } from "src/sections/omega/shell/switches";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import {
  newIdempotencyKey,
  retryIdempotent,
  useDeploymentMutation,
} from "../api/queries";
import AccessTokenNotice from "../components/AccessTokenNotice";
import DeployIcon from "../components/DeployIcon";
import MaskedValue from "../components/MaskedValue";
import { forgetAccessToken } from "../lib/accessTokens";
import { isBusy, liveRevisionOf } from "../lib/format";
import { deploymentsPath, newDeploymentPath } from "../lib/links";
import { Card } from "./OverviewTab";

const mono = { fontFamily: "monospace", fontSize: 13 };
function VariablesCard({ deployment }) {
  const navigate = useNavigate();
  const noDowntime = useOmegaPart(OMEGA_PART.DEPLOY_NO_DOWNTIME);
  const revision = liveRevisionOf(deployment) || deployment.revisions[0];
  const env = Object.entries(revision?.env || {});
  return (
    <Card
      title={`Environment · revision ${revision?.revision ?? "–"}`}
      testId="settings-env"
      action={
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => navigate(newDeploymentPath(deployment.id))}
          data-testid="change-settings"
        >
          Change settings
        </Button>
      }
    >
      <Typography typography="s2" color="text.secondary">
        Settings belong to a revision. Changing them deploys a new revision, so
        the project is uploaded again.{" "}
        <span data-testid="switchover-note">
          {switchoverSentence(noDowntime)}
        </span>
      </Typography>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography typography="s2" fontWeight="fontWeightMedium">
          Variables
        </Typography>
        {env.length ? (
          env.map(([name, value]) => (
            <Box
              key={name}
              data-testid="env-entry"
              sx={{
                display: "flex",
                alignItems: "baseline",
                gap: 1,
                minWidth: 0,
                ...mono,
              }}
            >
              <Box
                component="span"
                sx={{ color: "text.secondary", flexShrink: 0 }}
              >
                {name}
              </Box>
              <MaskedValue name={name} value={value} />
            </Box>
          ))
        ) : (
          <Typography typography="s2" color="text.secondary">
            None
          </Typography>
        )}
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography typography="s2" fontWeight="fontWeightMedium">
          Secrets from the vault
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={revision?.secretRefs?.length ? mono : undefined}
          data-testid="secret-refs"
        >
          {revision?.secretRefs?.length
            ? revision.secretRefs.join(", ")
            : "None"}
        </Typography>
      </Box>
    </Card>
  );
}

VariablesCard.propTypes = { deployment: PropTypes.object.isRequired };

function TokenCard({ deployment }) {
  const { enqueueSnackbar } = useSnackbar();
  const [confirm, setConfirm] = useState(false);
  const key = useRef(null);
  const rotate = useDeploymentMutation(
    (api) => api.rotateToken(deployment.id, { idempotencyKey: key.current }),
    {
      retry: retryIdempotent,
      onSuccess: () => {
        setConfirm(false);
        enqueueSnackbar("New access token minted", { variant: "success" });
      },
    },
  );
  return (
    <Card title="Access token" testId="settings-token">
      <Typography typography="s2" color="text.secondary">
        Studio and API clients send it as a bearer token. Rotating mints a new
        one, shown once, and restarts the live revision with it; the old token
        stops working.
      </Typography>
      <AccessTokenNotice id={deployment.id} />
      <Box>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          disabled={isBusy(deployment.status)}
          onClick={() => {
            key.current = newIdempotencyKey("token");
            rotate.reset();
            setConfirm(true);
          }}
          startIcon={<DeployIcon name="key" size={15} />}
          data-testid="rotate-token"
        >
          Rotate token
        </Button>
      </Box>
      <Dialog
        open={confirm}
        onClose={rotate.isPending ? undefined : () => setConfirm(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ typography: "m3" }}>
          Rotate the access token?
        </DialogTitle>
        <DialogContent>
          <Typography typography="s1" color="text.secondary">
            {deployment.liveRevision
              ? `Revision ${deployment.liveRevision} restarts with the new token. Clients using the old one are refused.`
              : "The new token applies when a revision next starts."}
          </Typography>
          {rotate.error && (
            <Alert severity="error" variant="outlined" sx={{ mt: 1.5 }}>
              {rotate.error.message}
            </Alert>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            color="inherit"
            onClick={() => setConfirm(false)}
            disabled={rotate.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            onClick={() => rotate.mutate()}
            disabled={rotate.isPending}
            data-testid="confirm-rotate"
          >
            {rotate.isPending ? "Rotating…" : "Rotate"}
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}

TokenCard.propTypes = { deployment: PropTypes.object.isRequired };

function DeleteCard({ deployment }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const remove = useDeploymentMutation((api) => api.remove(deployment.id), {
    removes: deployment.id,
    onSuccess: (deleted) => {
      forgetAccessToken(deployment.id);
      enqueueSnackbar(`${deleted?.name ?? deployment.name} deleted`, {
        variant: "success",
      });
      navigate(deploymentsPath());
    },
  });
  return (
    <Card title="Delete deployment" testId="settings-delete">
      <Typography typography="s2" color="text.secondary">
        Stops the server and removes its containers, images and run store. Every
        revision goes with it; this cannot be undone.
      </Typography>
      <Box>
        <Button
          size="small"
          variant="outlined"
          color="error"
          onClick={() => setOpen(true)}
          startIcon={<DeployIcon name="trash" size={15} />}
          data-testid="delete-deployment"
        >
          Delete
        </Button>
      </Box>
      <Dialog
        open={open}
        onClose={remove.isPending ? undefined : () => setOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ typography: "m3" }}>
          Delete {deployment.name}?
        </DialogTitle>
        <DialogContent
          sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
        >
          <Typography typography="s1" color="text.secondary">
            Type the deployment&apos;s name to confirm.
          </Typography>
          <TextField
            size="small"
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            inputProps={{
              "aria-label": "Deployment name to confirm",
              "data-testid": "delete-confirm-name",
              spellCheck: false,
            }}
          />
          {remove.error && (
            <Alert severity="error" variant="outlined">
              {remove.error.message}
            </Alert>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            color="inherit"
            onClick={() => setOpen(false)}
            disabled={remove.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={typed !== deployment.name || remove.isPending}
            onClick={() => remove.mutate()}
            data-testid="confirm-delete"
          >
            {remove.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}

DeleteCard.propTypes = { deployment: PropTypes.object.isRequired };

export default function SettingsTab({ deployment }) {
  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 2, maxWidth: 880 }}
    >
      <VariablesCard deployment={deployment} />
      <TokenCard deployment={deployment} />
      <DeleteCard deployment={deployment} />
    </Box>
  );
}

SettingsTab.propTypes = { deployment: PropTypes.object.isRequired };
