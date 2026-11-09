import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import ConfirmDialog from "../../components/ConfirmDialog";
import { GatewayStatusChip } from "../../components/frame";
import { apiErrorMessage } from "../api";
import { siemWrites, useSiemWrite } from "../hooks/useSiemExport";
import SiemDestinationForm from "./SiemDestinationForm";
import { SIEM_KINDS } from "./siemOptions";

export default function SiemDestination({
  destination,
  exportAvailable,
  canManage,
}) {
  const update = useSiemWrite(siemWrites.update);
  const remove = useSiemWrite(siemWrites.delete);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const on = destination.enabled;
  let host = "Invalid URL";
  try {
    host = new URL(destination.url).host;
  } catch {
    /* Never show a raw URL on a malformed row. */
  }
  const kind =
    SIEM_KINDS.find((item) => item.value === destination.kind)?.label ||
    destination.kind;
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1,
        }}
      >
        <Typography typography="s1">{destination.name}</Typography>
        <GatewayStatusChip
          tone={on ? "healthy" : "neutral"}
          label={on ? "On" : "Off"}
        />
      </Box>
      <Typography
        typography="s2"
        color="text.secondary"
      >{`${kind} · ${host}`}</Typography>
      <Typography
        typography="s2"
        color="text.secondary"
      >{`${destination.pending_count} pending · ${destination.dead_letter_count} failed (dead letters)`}</Typography>
      {destination.has_secret && (
        <Typography typography="s2" color="text.secondary">
          Credential configured (hidden)
        </Typography>
      )}
      {update.isError && (
        <Typography typography="s2" color="error.main" role="alert">
          {apiErrorMessage(update.error, "The destination was not changed.")}
        </Typography>
      )}
      {remove.isError && (
        <Typography typography="s2" color="error.main" role="alert">
          {apiErrorMessage(remove.error, "The destination was not deleted.")}
        </Typography>
      )}
      {canManage && (
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            size="small"
            variant="outlined"
            disabled={update.isPending || (!on && !exportAvailable)}
            onClick={() => update.mutate({ id: destination.id, enabled: !on })}
            aria-label={`${on ? "Turn off" : "Turn on"} ${destination.name}`}
          >
            {on ? "Turn off" : "Turn on"}
          </Button>
          <Button
            size="small"
            onClick={() => setEditing(true)}
            aria-label={`Edit ${destination.name}`}
          >
            Edit
          </Button>
          <Button
            size="small"
            onClick={() => setDeleting(true)}
            aria-label={`Delete ${destination.name}`}
          >
            Delete
          </Button>
        </Box>
      )}
      <Dialog
        open={editing}
        onClose={() => setEditing(false)}
        maxWidth="sm"
        fullWidth
        aria-labelledby={`edit-siem-${destination.id}`}
      >
        <DialogTitle id={`edit-siem-${destination.id}`}>
          Edit destination
        </DialogTitle>
        <DialogContent>
          {editing && (
            <Box sx={{ pt: 1 }}>
              <SiemDestinationForm
                destination={destination}
                onSaved={() => setEditing(false)}
              />
            </Box>
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        onConfirm={async () => {
          try {
            await remove.mutateAsync(destination.id);
            setDeleting(false);
          } catch {
            setDeleting(false);
          }
        }}
        title="Delete destination"
        message={`Delete ${destination.name} and its delivery history? This cannot be undone.`}
        confirmLabel="Delete"
        isLoading={remove.isPending}
      />
    </Box>
  );
}
SiemDestination.propTypes = {
  destination: PropTypes.object.isRequired,
  exportAvailable: PropTypes.bool,
  canManage: PropTypes.bool,
};
