import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import ConfirmDialog from "../../components/ConfirmDialog";
import { apiErrorMessage } from "../api";
import {
  decideRevision,
  listRevisions,
  readPinConfig,
  writePin,
} from "./revisionApi";
import PendingRevisions from "./PendingRevisions";
import AcceptedDefinitions from "./AcceptedDefinitions";
const panel = {
  p: 2,
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  minWidth: 0,
  overflowWrap: "anywhere",
};
const labels = {
  approve: "Approve",
  reject: "Reject",
  pin: "Pin",
  unpin: "Unpin",
};

export default function RevisionReview({ gatewayId, organizationId, canEdit }) {
  const client = useQueryClient();
  const key = ["gateway-mcp-revision-review", organizationId, gatewayId];
  const revisions = useQuery({
    queryKey: [...key, "pending"],
    queryFn: ({ signal }) => listRevisions(organizationId, signal),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const accepted = useQuery({
    queryKey: [...key, "approved"],
    queryFn: ({ signal }) => listRevisions(organizationId, signal, "approved"),
    enabled: revisions.isSuccess,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  // Listing can establish baseline pins. Read config only after both finish.
  const config = useQuery({
    queryKey: [...key, "pins"],
    queryFn: ({ signal }) => readPinConfig(gatewayId, organizationId, signal),
    enabled: revisions.isSuccess && accepted.isSuccess,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(null);
  const rows = revisions.data?.revisions || [];
  const pinning = config.data?.mcp?.pinning || {};
  // Match the gateway: absent or unknown values default to quarantine.
  const mode = ["off", "warn"].includes(pinning.mode)
    ? pinning.mode
    : "quarantine";
  const allowNew = pinning.new_tools === "allow";
  const heldByPolicy = (row) =>
    mode === "quarantine" &&
    (row.reason === "changed" || (row.reason === "new" && !allowNew));
  const pins = pinning.pins || {};
  const failed = revisions.isError || accepted.isError || config.isError;
  const loading =
    !failed && (revisions.isPending || accepted.isPending || config.isPending);
  const stale =
    revisions.data?.gateway_synced === false ||
    accepted.data?.gateway_synced === false;
  const disabled =
    !canEdit ||
    busy ||
    loading ||
    failed ||
    stale ||
    revisions.isFetching ||
    accepted.isFetching ||
    config.isFetching;

  function choose(action, row) {
    if (disabled) return;
    setError("");
    setNotice(null);
    const pinKey =
      action === "unpin" ? row.key : `${row.server_id}/${row.tool}`;
    const serverId = pinKey.split("/")[0];
    const lastPin =
      Object.keys(pins).filter((name) => name.startsWith(`${serverId}/`))
        .length === 1;
    setSelected({
      action,
      row,
      pinKey,
      disableBaseline:
        action === "unpin" && lastPin && pinning.auto_baseline !== false,
    });
  }

  async function refresh() {
    // A revision list can baseline a server, so always refresh its pins too.
    await revisions.refetch();
    await accepted.refetch();
    await config.refetch();
  }

  async function save() {
    if (!selected || disabled || lock.current) return;
    const { action, row, pinKey, disableBaseline } = selected;
    if (
      (action === "approve" || action === "reject") &&
      !rows.some(
        (r) =>
          r.id === row.id &&
          r.revision === row.revision &&
          r.status === "pending",
      )
    ) {
      setError(
        "This revision changed while you were reviewing it. Refresh and review again.",
      );
      return;
    }
    if (action === "unpin" && pins[pinKey] !== row.revision) {
      setError(
        "This pin changed while you were reviewing it. Refresh and review again.",
      );
      return;
    }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const result =
        action === "approve" || action === "reject"
          ? await decideRevision(action, row, organizationId)
          : await writePin(gatewayId, organizationId, {
              pins: {
                [pinKey]: action === "pin" ? row.previous_revision : null,
              },
              ...(disableBaseline ? { auto_baseline: false } : {}),
            });
      setSelected(null);
      setNotice(
        result.gateway_synced === false
          ? {
              severity: "warning",
              text: "Saved, but not yet applied by the gateway. Refresh after synchronization; do not assume the tool is available.",
            }
          : {
              severity: "success",
              text: {
                approve: "Revision approved and pinned.",
                reject: heldByPolicy(row)
                  ? "Revision rejected. Under the current pinning policy, this tool remains held."
                  : "Revision rejected. The current pinning policy still serves this tool.",
                pin: "Accepted revision pinned.",
                unpin:
                  "Pin removed. The gateway's new-tool policy still applies.",
              }[action],
            },
      );
      await client.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      await client.invalidateQueries({ queryKey: ["agentcc-mcp-tools"] });
      await refresh();
    } catch (err) {
      setError(
        apiErrorMessage(
          err,
          "The change could not be saved. Refresh and try again.",
        ),
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  const message = selected
    ? {
        approve:
          "Approve this exact proposed definition and pin it for the organization? This records acceptance and updates the pin once the gateway applies the decision.",
        reject: `Reject this proposed definition? ${heldByPolicy(selected.row) ? "Under the current pinning policy, this tool stays held." : "The current pinning policy still serves this tool."} Rejection records the decision without changing the pinning policy or rolling back the upstream server.`,
        pin: `Pin the accepted definition shown in this review, not the proposed change? ${mode === "quarantine" ? "A different upstream definition will be held once the gateway applies this pin." : "The current pinning mode still serves a different upstream definition."}`,
        unpin: `Remove this tool's accepted pin? This does not approve its current definition; the new-tool policy still applies.${selected.disableBaseline ? " Removing the last pin for this server also disables automatic baselines for this organization, so it is not immediately pinned again. Other existing pins are kept." : ""}`,
      }[selected.action]
    : "";

  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        spacing={1}
      >
        <Box>
          <Typography component="h2" variant="subtitle1">
            Tool revisions
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Review changes to your servers’ tools before approving a new pinned
            definition.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          onClick={refresh}
          disabled={
            busy ||
            revisions.isFetching ||
            accepted.isFetching ||
            config.isFetching
          }
        >
          Refresh
        </Button>
      </Stack>
      {!canEdit && (
        <Alert severity="info">
          Only an organization owner or admin can approve, reject or change
          pins.
        </Alert>
      )}
      {mode === "off" && (
        <Alert severity="warning">
          Tool pinning is off in this organization’s gateway configuration.
          Stored pins do not block changed tools.
        </Alert>
      )}
      {mode === "warn" && (
        <Alert severity="warning">
          Tool pinning is in warn mode. The gateway still serves changed and new
          tools without approval; rejecting a revision does not block it.
        </Alert>
      )}
      {allowNew && (
        <Alert severity="warning">
          New tools are allowed by the pinning policy. The gateway still serves
          unpinned new tools without approval. Once a tool is pinned, a changed
          definition follows the pinning mode.
        </Alert>
      )}
      {notice && <Alert severity={notice.severity}>{notice.text}</Alert>}
      {loading && (
        <Stack direction="row" spacing={1}>
          <CircularProgress size={20} />
          <Typography role="status" variant="body2">
            Loading tool revisions…
          </Typography>
        </Stack>
      )}
      {failed && (
        <Alert
          severity="error"
          action={<Button onClick={refresh}>Retry</Button>}
        >
          Could not load tool revisions or pins. Refresh to try again.
        </Alert>
      )}
      {stale && (
        <Alert severity="warning">
          Showing the last synchronized revisions. The gateway could not be
          reached; refresh before making a decision.
        </Alert>
      )}
      {!loading && !failed && (
        <>
          <Typography component="h3" variant="subtitle2">
            Pending review
          </Typography>
          {rows.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No pending tool revisions.
            </Typography>
          )}
          {rows.length >= 500 && (
            <Alert severity="info">
              Showing the 500 most recent pending revisions. Review these and
              refresh for older changes.
            </Alert>
          )}
          <PendingRevisions
            rows={rows}
            pins={pins}
            disabled={disabled}
            onChoose={choose}
          />
          <AcceptedDefinitions
            rows={accepted.data?.revisions || []}
            pins={pins}
            disabled={disabled}
            onPin={(row) => choose("pin", row)}
          />
          {accepted.data?.revisions?.length >= 500 && (
            <Alert severity="info">
              Showing the 500 most recent accepted definitions.
            </Alert>
          )}
          <Typography component="h3" variant="subtitle2">
            Accepted pins
          </Typography>
          {Object.keys(pins).length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No stored tool pins.
            </Typography>
          )}
          {Object.entries(pins)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([name, revision]) => (
              <Stack
                component="article"
                aria-label={`${name} pin`}
                key={name}
                spacing={1}
                sx={panel}
              >
                <Typography variant="subtitle2">{name}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {revision}
                </Typography>
                <Box>
                  <Button
                    disabled={disabled}
                    onClick={() => choose("unpin", { key: name, revision })}
                  >
                    Unpin
                  </Button>
                </Box>
              </Stack>
            ))}
        </>
      )}
      {error && !selected && <Alert severity="error">{error}</Alert>}
      <ConfirmDialog
        open={Boolean(selected)}
        onClose={() => {
          if (!busy) {
            setSelected(null);
            setError("");
          }
        }}
        title={`${labels[selected?.action] || "Review"} ${selected?.row.tool || selected?.pinKey || "revision"}?`}
        message={error || message}
        confirmLabel={labels[selected?.action] || "Confirm"}
        confirmColor={
          selected?.action === "approve" || selected?.action === "pin"
            ? "primary"
            : "error"
        }
        isLoading={busy || (Boolean(selected) && disabled)}
        onConfirm={save}
      />
    </Stack>
  );
}
RevisionReview.propTypes = {
  gatewayId: PropTypes.string.isRequired,
  organizationId: PropTypes.string.isRequired,
  canEdit: PropTypes.bool.isRequired,
};
