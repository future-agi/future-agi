import React, { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import { GatewayStatusChip } from "../../components/frame";
import GatewayEmptyState from "../../components/GatewayEmptyState";
import ConfirmDialog from "../../components/ConfirmDialog";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { useMCPStatus } from "../hooks/useMCPConfig";
import {
  approvalRoutesReady,
  useApprovalDecision,
  useApprovals,
} from "./useApprovals";

const date = (value) =>
  value ? new Date(value).toLocaleString() : "Not decided";

// Only server-provided identifiers and the fingerprint, never raw arguments.
const bindingText = (row) =>
  `Argument fingerprint: ${row?.args_hash || "Unavailable"}. Requesting principal kind: ${row?.principal?.kind || "Unavailable"}. Key ID: ${row?.principal?.key_id || "Not provided"}. User ID: ${row?.principal?.user_id || "Not provided"}. Client ID: ${row?.principal?.client_id || "Not provided"}.`;
const reviewable = (row) =>
  /^[a-f0-9]{64}$/i.test(row?.args_hash || "") &&
  Boolean(row?.principal?.kind) &&
  Boolean(
    row?.principal?.key_id ||
      row?.principal?.user_id ||
      row?.principal?.client_id,
  );
const bindingKey = (row) =>
  JSON.stringify([
    row?.approval_id,
    row?.org_id,
    row?.tool,
    row?.virtual_server_id,
    row?.agent_run_id,
    row?.expires_at,
    bindingText(row),
  ]);

export default function ApprovalsTab({ gatewayId }) {
  const status = useMCPStatus(gatewayId);
  const enabled = !status.isError && status.data?.features?.approvals === true;
  const query = useApprovals(gatewayId, enabled);
  const mutation = useApprovalDecision(gatewayId);
  const { canEdit, disabledReason } = useGatewayAdmin();
  const [selection, setSelection] = useState(null);
  const [error, setError] = useState("");
  if (!enabled)
    return (
      <Alert severity="warning">
        Approval capability could not be confirmed. Refresh the gateway status
        before reviewing a request.
      </Alert>
    );
  if (!approvalRoutesReady())
    return (
      <Alert severity="warning">
        Approval routes are not installed on this console. No decision can be
        submitted.
      </Alert>
    );
  const decide = async () => {
    const row = query.data?.find(
      (item) => item.approval_id === selection?.row.approval_id,
    );
    if (
      !canEdit ||
      mutation.isPending ||
      query.isError ||
      row?.status !== "pending" ||
      !reviewable(row) ||
      bindingKey(row) !== selection?.binding ||
      !(Date.parse(row.expires_at) > Date.now())
    )
      return;
    setError("");
    try {
      await mutation.mutateAsync({
        approvalId: row.approval_id,
        decision: selection.decision,
      });
      setSelection(null);
    } catch {
      setSelection(null);
      setError("The decision could not be confirmed. Refresh before retrying.");
    }
  };
  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 2,
        }}
      >
        <Typography variant="h6">Tool approvals</Typography>
        <Button
          variant="outlined"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
        >
          Refresh
        </Button>
      </Box>
      <Typography typography="s2" color="text.secondary">
        Decisions apply only to the original tool, arguments, person-owned key
        and run. Approval does not run the tool; the agent must retry.
      </Typography>
      {!canEdit && <Alert severity="info">{disabledReason}</Alert>}
      {error && <Alert severity="error">{error}</Alert>}
      {query.isError && (
        <Alert severity="error">
          Approval state could not be refreshed. Decisions are disabled until
          Refresh succeeds.
        </Alert>
      )}
      {query.isLoading && (
        <Typography role="status">Loading approvals</Typography>
      )}
      {!query.isLoading && !query.isError && query.data?.length === 0 && (
        <GatewayEmptyState
          title="No approval requests"
          description="Requests appear when an agent calls a tool covered by a virtual server's approval policy."
        />
      )}
      {(query.data || []).map((row) => {
        const expired = Date.parse(row.expires_at) <= Date.now();
        const currentStatus = expired ? "expired" : row.status;
        const disabled =
          !canEdit ||
          query.isError ||
          mutation.isPending ||
          !reviewable(row) ||
          !(Date.parse(row.expires_at) > Date.now()) ||
          currentStatus !== "pending";
        return (
          <Box
            component="article"
            aria-label={`Approval for ${row.tool}`}
            key={row.approval_id}
            sx={{
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              p: 2,
              overflowWrap: "anywhere",
            }}
          >
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              sx={{
                justifyContent: "space-between",
                alignItems: { sm: "center" },
              }}
            >
              <Typography typography="s1">{row.tool}</Typography>
              <GatewayStatusChip
                tone={currentStatus === "approved" ? "healthy" : "neutral"}
                label={currentStatus}
              />
            </Stack>
            <Typography typography="s2" color="text.secondary">
              Run: {row.agent_run_id || "No run id"} · Virtual server:{" "}
              {row.virtual_server_id}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {bindingText(row)}
            </Typography>
            {!reviewable(row) && (
              <Alert severity="warning">
                Request identity is incomplete. Refresh before deciding.
              </Alert>
            )}
            <Typography typography="s2" color="text.secondary">
              Requested: {date(row.requested_at)} · Expires:{" "}
              {date(row.expires_at)}
            </Typography>
            {row.decided_at && (
              <Typography typography="s2">
                Decided by {row.decided_by || "Former member"} at{" "}
                {date(row.decided_at)}
              </Typography>
            )}
            {row.note && <Typography typography="s2">{row.note}</Typography>}
            <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
              <Button
                variant="contained"
                disabled={disabled}
                onClick={() =>
                  setSelection({
                    row,
                    binding: bindingKey(row),
                    decision: "approve",
                  })
                }
              >
                Approve
              </Button>
              <Button
                variant="outlined"
                color="error"
                disabled={disabled}
                onClick={() =>
                  setSelection({
                    row,
                    binding: bindingKey(row),
                    decision: "deny",
                  })
                }
              >
                Deny
              </Button>
            </Stack>
          </Box>
        );
      })}
      <ConfirmDialog
        open={Boolean(selection) && canEdit}
        onClose={() => setSelection(null)}
        onConfirm={decide}
        title={
          selection?.decision === "approve"
            ? "Approve tool call"
            : "Deny tool call"
        }
        message={`${selection?.row.tool || "This tool"} for run ${selection?.row.agent_run_id || "without a run id"}. ${bindingText(selection?.row)} The gateway rechecks status and expiry before accepting this decision.`}
        confirmLabel={
          selection?.decision === "approve"
            ? "Confirm approval"
            : "Confirm denial"
        }
        confirmColor={selection?.decision === "approve" ? "primary" : "error"}
        isLoading={mutation.isPending}
      />
    </Stack>
  );
}
ApprovalsTab.propTypes = { gatewayId: PropTypes.string };
