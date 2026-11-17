import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { useSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";

import {
  newIdempotencyKey,
  retryIdempotent,
  useSandboxMutation,
  useSnapshots,
} from "../api/queries";
import SandboxErrorState from "../components/SandboxErrorState";
import SandboxIcon from "../components/SandboxIcon";
import { LoadingRows } from "../components/SessionGate";
import StatePanel from "../components/StatePanel";
import { formatDateTime, shortId, timeAgo } from "../lib/format";
import { sandboxPath } from "../lib/links";
import { sandboxErrorText } from "../lib/offerCopy";

const mono = { fontFamily: "monospace", fontSize: 12 };

function RestoreDialog({ sandbox, snapshot, onClose }) {
  const navigate = useNavigate();
  const [name, setName] = useState(`${sandbox.name} (restored)`.slice(0, 120));
  // One key per dialog: a retried restore replays EE's first new sandbox.
  const key = useRef(newIdempotencyKey());
  const restore = useSandboxMutation(
    (api) =>
      api.restore(
        sandbox.id,
        snapshot.id,
        name.trim() ? { name: name.trim() } : {},
        { idempotencyKey: key.current },
      ),
    {
      retry: retryIdempotent,
      onSuccess: (created) => {
        onClose();
        navigate(sandboxPath(created.id));
      },
    },
  );
  return (
    <Dialog
      open
      onClose={restore.isPending ? undefined : onClose}
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle sx={{ typography: "m3" }}>
        Restore as a new sandbox
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography typography="s1" color="text.secondary">
          A new sandbox starts with this snapshot&apos;s files, {sandbox.name}
          &apos;s image and its limits.
          {` ${snapshot.statement}`}
        </Typography>
        <TextField
          size="small"
          label="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          inputProps={{ maxLength: 120, "data-testid": "restore-name" }}
        />
        {restore.error && (
          <Typography
            typography="s2"
            color="error.main"
            data-testid="restore-error"
          >
            {sandboxErrorText(restore.error)}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={restore.isPending}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          loading={restore.isPending}
          onClick={() => restore.mutate()}
          data-testid="restore-confirm"
        >
          Restore
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

RestoreDialog.propTypes = {
  sandbox: PropTypes.object.isRequired,
  snapshot: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default function SnapshotsTab({ sandbox }) {
  const { enqueueSnackbar } = useSnackbar();
  const snapshots = useSnapshots(sandbox.id, !sandbox.readOnly);
  const [restoring, setRestoring] = useState(null);
  const takeKey = useRef(null);
  const take = useSandboxMutation(
    (api) => api.snapshot(sandbox.id, { idempotencyKey: takeKey.current }),
    {
      retry: retryIdempotent,
      onSuccess: () =>
        enqueueSnackbar("Snapshot taken", { variant: "success" }),
      onError: (error) => enqueueSnackbar(error.message, { variant: "error" }),
    },
  );
  const statement = sandbox.provider.snapshotStatement;

  if (sandbox.readOnly) {
    return (
      <StatePanel
        compact
        icon="camera"
        title="Snapshots belong to the run"
        description="Sandboxes of agent runs are read-only here."
      />
    );
  }

  const list = snapshots.data ?? [];
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: { xs: "flex-start", sm: "center" },
          gap: 1.5,
          flexDirection: { xs: "column", sm: "row" },
        }}
      >
        <Box>
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Snapshots
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="snapshot-statement"
          >
            {statement}
          </Typography>
        </Box>
        <Tooltip
          title={
            sandbox.state === "active"
              ? ""
              : sandbox.state === "destroyed"
                ? "A deleted sandbox can't take snapshots. Retained snapshots can be restored as a new sandbox only before the original sandbox's lifetime expires."
                : "Resume the sandbox to take a snapshot"
          }
        >
          <span>
            <LoadingButton
              size="small"
              variant="outlined"
              color="inherit"
              loading={take.isPending}
              disabled={sandbox.state !== "active"}
              onClick={() => {
                takeKey.current = newIdempotencyKey();
                take.mutate();
              }}
              startIcon={<SandboxIcon name="camera" size={14} />}
              data-testid="snapshot-take"
            >
              Take snapshot
            </LoadingButton>
          </span>
        </Tooltip>
      </Box>
      {snapshots.isLoading && <LoadingRows rows={2} />}
      {snapshots.error && (
        <SandboxErrorState
          compact
          error={snapshots.error}
          onRetry={() => snapshots.refetch()}
        />
      )}
      {!snapshots.isLoading && !snapshots.error && list.length === 0 && (
        <StatePanel
          compact
          icon="camera"
          title="No snapshots yet"
          description="A snapshot saves this sandbox's files so you can restore them as a new sandbox."
        />
      )}
      {list.length > 0 && (
        <Box
          component="ul"
          sx={{
            listStyle: "none",
            m: 0,
            p: 0,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
          data-testid="snapshots-list"
        >
          {list.map((snapshot, index) => (
            <Box
              component="li"
              key={snapshot.id}
              data-testid="snapshot-row"
              sx={{
                px: 2,
                py: 1.5,
                display: "flex",
                alignItems: { xs: "flex-start", sm: "center" },
                justifyContent: "space-between",
                gap: 1.5,
                flexDirection: { xs: "column", sm: "row" },
                borderTop: index ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  typography="s1"
                  title={formatDateTime(snapshot.createdAt)}
                >
                  Files snapshot · {timeAgo(snapshot.createdAt)}
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ ...mono, overflowWrap: "anywhere" }}
                >
                  {snapshot.id} · content{" "}
                  {shortId(
                    snapshot.contentAddress.sha256 ||
                      snapshot.contentAddress.artifactId,
                    12,
                  )}
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                startIcon={<SandboxIcon name="restore" size={14} />}
                onClick={() => setRestoring(snapshot)}
                data-testid="snapshot-restore"
                sx={{ flexShrink: 0 }}
              >
                Restore as new
              </Button>
            </Box>
          ))}
        </Box>
      )}
      {restoring && (
        <RestoreDialog
          sandbox={sandbox}
          snapshot={restoring}
          onClose={() => setRestoring(null)}
        />
      )}
    </Box>
  );
}

SnapshotsTab.propTypes = { sandbox: PropTypes.object.isRequired };
