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
import {
  isMCPGuardrailsChanged,
  useMCPStatus,
  useUpdateMCPGuardrails,
} from "../hooks/useMCPConfig";
import { mcpGuardrailRows } from "./storedGuardrails";
import { isStored } from "../../settings/ui/storedValues";
import StoredSettingsDialog from "./StoredSettingsDialog";
import { STORED_MCP_NOTICE } from "./notices";

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
// The server ids the MCP status lists for the organization, for the
// Allowed servers suggestions.
const statusServerIds = (status) =>
  (Array.isArray(status?.servers) ? status.servers : [])
    .map((server) => server?.server_id)
    .filter((id) => typeof id === "string" && id);
// A Turn off refused because another save changed the stored settings after
// this page read them. Its row has no Reload; the tab reads them again itself.
// NEW: site claims review required for this refusal sentence.
const TURN_OFF_CHANGED =
  "Someone changed these guardrail settings after this page loaded them, so nothing was turned off. Check the saved settings before you try again.";

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
  const { data: mcpStatus } = useMCPStatus(gatewayId);
  const update = useUpdateMCPGuardrails();
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  // The stored settings the open dialog's save is made on, and whether its
  // last save was refused because they changed after it opened.
  const [openedBase, setOpenedBase] = useState();
  const [changed, setChanged] = useState(false);

  if (isLoading) return <Skeleton variant="rounded" height={240} />;
  // A re-read that fails while the dialog is open keeps the dialog and its
  // draft; the error state shows once the dialog is closed.
  if (isError && !editing) {
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
      { gatewayId, config: next, base: openedBase },
      {
        onSuccess: () => {
          setEditing(false);
          // NEW: storage-only completion, not a claim of enforcement.
          enqueueSnackbar("MCP guardrail settings saved", {
            variant: "success",
          });
        },
        onError: (err) => {
          // Refused because another save changed the stored settings after
          // the dialog opened: nothing was written. The dialog says so and
          // keeps the draft; every other failure keeps its toast.
          if (isMCPGuardrailsChanged(err)) {
            setChanged(true);
            return;
          }
          enqueueSnackbar(
            apiErrorMessage(
              err,
              "Stored MCP guardrail settings could not be saved.",
            ),
            { variant: "error" },
          );
        },
      },
    );
  };
  // Re-reads the stored settings into the summary and resolves to them
  // ({ stored }), so the dialog rebases its draft on them; its next save is
  // made on what was read. null when the re-read failed.
  const reload = async () => {
    const result = await refetch?.();
    if (!result || result.isError) return null;
    const reread = result.data?.mcp?.guardrails;
    setOpenedBase(reread);
    setChanged(false);
    return { stored: reread };
  };
  const close = () => {
    setEditing(false);
    // A refused save the person did not Reload after: the summary still shows
    // the settings this page read before, and the next Edit would open on
    // them. Read them again.
    if (changed) refetch?.();
  };
  const turnOff = () =>
    update.mutate(
      { gatewayId, config: { ...stored, enabled: false }, base: stored },
      {
        onSuccess: () => {
          setConfirming(false);
          enqueueSnackbar("Stored MCP guardrails turned off", {
            variant: "success",
          });
        },
        onError: (err) => {
          // Changed since this page read them: nothing was turned off. Say
          // so, and read them again, so that a second Turn off is made on the
          // saved version.
          if (isMCPGuardrailsChanged(err)) {
            enqueueSnackbar(TURN_OFF_CHANGED, { variant: "error" });
            refetch?.();
            return;
          }
          enqueueSnackbar(
            apiErrorMessage(err, "The MCP guardrails could not be turned off."),
            { variant: "error" },
          );
        },
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
              setOpenedBase(stored);
              setChanged(false);
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
          changed={changed}
          connectedServers={statusServerIds(mcpStatus)}
          onReload={reload}
          onClose={close}
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
