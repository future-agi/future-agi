import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { switchoverSentence } from "src/sections/omega/shell/switches";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import {
  newIdempotencyKey,
  retryIdempotent,
  useDeploymentMutation,
} from "../api/queries";
import DeployIcon from "../components/DeployIcon";
import { StatusChip } from "../components/StatusChip";
import {
  absoluteTime,
  actionLabel,
  isBusy,
  relativeTime,
  shortDigest,
} from "../lib/format";

const mono = { fontFamily: "monospace", fontSize: 12.5 };

/** A revision can be served again only if it served before and is not serving now. */
// eslint-disable-next-line react-refresh/only-export-components
export const canRollBackTo = (deployment, revision) =>
  Boolean(revision.liveAt) &&
  revision.revision !== deployment.liveRevision &&
  deployment.liveRevision !== null &&
  !isBusy(deployment.status);

function RollbackDialog({ deployment, target, onClose }) {
  const { enqueueSnackbar } = useSnackbar();
  const noDowntime = useOmegaPart(OMEGA_PART.DEPLOY_NO_DOWNTIME);
  // One key per decision: a retried send replays EE's first answer.
  const key = useRef(newIdempotencyKey("rollback"));
  const expected = useRef(deployment.liveRevision);
  const rollback = useDeploymentMutation(
    (api) =>
      api.rollback(
        deployment.id,
        target.revision,
        { expectedRevision: expected.current },
        { idempotencyKey: key.current },
      ),
    {
      retry: retryIdempotent,
      onSuccess: () => {
        enqueueSnackbar(`Rolling back to revision ${target.revision}`, {
          variant: "info",
        });
        onClose();
      },
    },
  );
  return (
    <Dialog
      open
      onClose={rollback.isPending ? undefined : onClose}
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle sx={{ typography: "m3" }}>
        Roll back to revision {target.revision}?
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
      >
        <Typography typography="s1" color="text.secondary">
          Revision {expected.current} is live now. Revision {target.revision}{" "}
          starts again with the bundle and settings it was deployed with.{" "}
          <span data-testid="switchover-note">
            {switchoverSentence(noDowntime, target.revision)}
          </span>{" "}
          If anything changed since you opened this, nothing happens.
        </Typography>
        {rollback.error && (
          <Alert
            severity="error"
            variant="outlined"
            data-testid="rollback-error"
          >
            {rollback.error.message}
          </Alert>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button color="inherit" onClick={onClose} disabled={rollback.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={() => rollback.mutate()}
          disabled={rollback.isPending}
          data-testid="confirm-rollback"
        >
          {rollback.isPending
            ? "Rolling back…"
            : `Roll back to revision ${target.revision}`}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RollbackDialog.propTypes = {
  deployment: PropTypes.object.isRequired,
  target: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

function RevisionRow({ deployment, revision, onRollback }) {
  const env = Object.keys(revision.env || {});
  return (
    <Box
      data-testid="revision-row"
      data-revision={revision.revision}
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "1fr",
          md: "minmax(0, 1.2fr) minmax(0, 2fr) 140px",
        },
        alignItems: "center",
        gap: { xs: 1, md: 2 },
        px: 2,
        py: 1.5,
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-of-type": { borderBottom: 0 },
      }}
    >
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
      >
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Revision {revision.revision}
        </Typography>
        <StatusChip status={revision.status} testId="revision-status" />
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          title={revision.createdAt}
        >
          Deployed {relativeTime(revision.createdAt)} ·{" "}
          <Box component="span" sx={mono}>
            {shortDigest(revision.bundle.sha256)}
          </Box>{" "}
          · {revision.bundle.files} files
        </Typography>
        <Typography typography="s2" color="text.secondary" noWrap>
          {env.length
            ? `${env.length} ${env.length === 1 ? "variable" : "variables"}`
            : "No variables"}{" "}
          ·{" "}
          {revision.secretRefs.length
            ? `secrets ${revision.secretRefs.join(", ")}`
            : "no secrets"}
        </Typography>
        {revision.message && revision.status === "failed" && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {revision.message}
          </Typography>
        )}
      </Box>
      <Box sx={{ justifySelf: { xs: "start", md: "end" } }}>
        {canRollBackTo(deployment, revision) && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onRollback(revision)}
            startIcon={<DeployIcon name="rollback" size={15} />}
            data-testid="rollback"
          >
            Roll back
          </Button>
        )}
      </Box>
    </Box>
  );
}

RevisionRow.propTypes = {
  deployment: PropTypes.object.isRequired,
  revision: PropTypes.object.isRequired,
  onRollback: PropTypes.func.isRequired,
};

function History({ history }) {
  if (!history.length) return null;
  return (
    <Box>
      <Typography typography="s1" fontWeight="fontWeightMedium" sx={{ mb: 1 }}>
        History
      </Typography>
      <Box
        component="ol"
        data-testid="deployment-history"
        sx={{
          listStyle: "none",
          m: 0,
          p: 0,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {[...history].reverse().map((entry) => (
          <Box
            component="li"
            key={entry.seq}
            data-testid="history-entry"
            data-action={entry.action}
            data-revision={entry.revision}
            sx={{
              display: "flex",
              justifyContent: "space-between",
              gap: 2,
              px: 2,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              "&:last-of-type": { borderBottom: 0 },
            }}
          >
            <Typography
              typography="s2"
              color="text.primary"
              sx={{ minWidth: 0 }}
            >
              {actionLabel(entry.action)}: revision {entry.revision}
              {entry.previousRevision
                ? ` (from ${entry.previousRevision})`
                : ""}{" "}
              <Box
                component="span"
                sx={{
                  color:
                    entry.outcome === "failed" ? "error.main" : "success.main",
                }}
              >
                {entry.outcome === "failed" ? "failed" : "live"}
              </Box>
            </Typography>
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ flexShrink: 0 }}
              title={entry.at}
            >
              {absoluteTime(entry.at)}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

History.propTypes = { history: PropTypes.array.isRequired };

export default function RevisionsTab({ deployment }) {
  const [target, setTarget] = useState(null);
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <Box
        data-testid="revisions-list"
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
        }}
      >
        {deployment.revisions.map((revision) => (
          <RevisionRow
            key={revision.revision}
            deployment={deployment}
            revision={revision}
            onRollback={setTarget}
          />
        ))}
      </Box>
      <History history={deployment.history || []} />
      {target && (
        <RollbackDialog
          deployment={deployment}
          target={target}
          onClose={() => setTarget(null)}
        />
      )}
    </Box>
  );
}

RevisionsTab.propTypes = { deployment: PropTypes.object.isRequired };
