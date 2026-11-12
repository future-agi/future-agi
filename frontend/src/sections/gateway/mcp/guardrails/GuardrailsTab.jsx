import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { GatewayEmptyState } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import RecordedOnly from "../../settings/ui/RecordedOnly";
import { apiErrorMessage } from "../api";
import { useUpdateMCPGuardrails } from "../hooks/useMCPConfig";
import { mcpGuardrailRows } from "./storedGuardrails";
import { isStored } from "../../settings/ui/storedValues";
import StoredSettingsDialog from "./StoredSettingsDialog";
import { STORED_MCP_NOTICE } from "./notices";

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

// 850: always reachable, with an empty state and v1's working storage edits.
// Org MCP guardrails do not enforce tool policy in either lineage. Label
// edits as stored settings, never activation; keep the existing off confirmation.
// Failed reads are not evidence of an empty configuration.
const GuardrailsTab = ({ gatewayId }) => {
  const {
    data: config,
    isLoading,
    isError,
    refetch,
  } = useGatewayConfig(gatewayId);
  const { canEdit } = useGatewayAdmin();
  const update = useUpdateMCPGuardrails();
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);

  if (isLoading) return <Skeleton variant="rounded" height={240} />;
  if (isError) {
    return (
      <GatewayEmptyState
        icon="monitoring"
        title="Organization MCP guardrails could not be loaded"
        description="The organization's gateway config did not load. Try again."
        actionLabel="Try again"
        onAction={() => refetch?.()}
      />
    );
  }

  const stored = config?.mcp?.guardrails;
  const isOn = isPlainObject(stored) && stored.enabled === true;
  const editableShape = stored == null || isPlainObject(stored);
  const save = (next) => {
    if (!canEdit || update.isPending || !editableShape) return;
    update.mutate(
      { gatewayId, config: next },
      {
        onSuccess: () => {
          setEditing(false);
          // NEW: storage-only completion, not a claim of enforcement.
          enqueueSnackbar("MCP guardrail settings saved", {
            variant: "success",
          });
        },
        onError: (err) =>
          enqueueSnackbar(
            apiErrorMessage(
              err,
              "Stored MCP guardrail settings could not be saved.",
            ),
            { variant: "error" },
          ),
      },
    );
  };
  const turnOff = () =>
    update.mutate(
      { gatewayId, config: { ...stored, enabled: false } },
      {
        onSuccess: () => {
          setConfirming(false);
          enqueueSnackbar("Stored MCP guardrails turned off", {
            variant: "success",
          });
        },
        onError: (err) =>
          enqueueSnackbar(
            apiErrorMessage(err, "The MCP guardrails could not be turned off."),
            { variant: "error" },
          ),
      },
    );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography typography="s2" color="text.secondary">
        {STORED_MCP_NOTICE}
      </Typography>
      <RecordedOnly
        ids={["mcp.guardrails"]}
        rows={mcpGuardrailRows(stored)}
        title="Organization MCP guardrails"
        description={null}
        emptyText="No displayable MCP guardrail settings."
        testId="mcp-guardrails-recorded-only"
      />
      {/* Sized to its label, as every gateway button is: the column box
          would stretch it across the content width. */}
      <Box sx={{ alignSelf: "flex-start" }}>
        <GatewayAdminOnly allowed={canEdit}>
          {/* NEW: these actions edit stored settings only. */}
          <Button
            size="small"
            variant="outlined"
            // Its own width, not the column's.
            sx={{ alignSelf: "flex-start" }}
            disabled={!canEdit || update.isPending || !editableShape}
            onClick={() => {
              setConfirming(false);
              setEditing(true);
            }}
          >
            {isStored(stored) ? "Edit stored settings" : "Add stored settings"}
          </Button>
        </GatewayAdminOnly>
      </Box>
      {!editableShape && (
        // NEW: do not replace an unrecognized stored shape with editor defaults.
        <Typography typography="s2" color="text.secondary">
          This stored format is read-only here.
        </Typography>
      )}
      {editing && (
        <StoredSettingsDialog
          stored={stored}
          busy={update.isPending}
          canEdit={canEdit}
          onClose={() => setEditing(false)}
          onSave={save}
        />
      )}
      {isOn && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          {confirming ? (
            <>
              <Typography typography="s2" color="text.secondary">
                {/* NEW: storage-only confirmation; editing can turn the saved flag on again. */}
                Turn the stored MCP guardrails off? Every other stored value is
                kept.
              </Typography>
              <Button
                size="small"
                variant="outlined"
                disabled={update.isPending}
                onClick={turnOff}
              >
                Turn off
              </Button>
              <Button
                size="small"
                disabled={update.isPending}
                onClick={() => setConfirming(false)}
              >
                Keep on
              </Button>
            </>
          ) : (
            <GatewayAdminOnly allowed={canEdit}>
              <Button
                size="small"
                variant="outlined"
                disabled={!canEdit}
                onClick={() => setConfirming(true)}
              >
                Turn off
              </Button>
            </GatewayAdminOnly>
          )}
        </Box>
      )}
    </Box>
  );
};

GuardrailsTab.propTypes = {
  gatewayId: PropTypes.string,
};

export default GuardrailsTab;
