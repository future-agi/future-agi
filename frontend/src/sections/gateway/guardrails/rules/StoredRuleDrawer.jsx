import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack, Typography } from "@mui/material";
import { GatewayDetailDrawer, GatewayStatusChip } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ConfirmDialog from "../../components/ConfirmDialog";
import {
  STORED_ONLY_OVERVIEW_HELP,
  storedOnlyOffMessage,
} from "../../constants/storedOnlyChecks";
import {
  storedSettings,
  storedToolSettings,
  NEVER_TRIGGERS_WARNING,
} from "../../settings/storedCheckSummary";
import { GUARDRAILS_OFF_BANNER } from "../../utils/guardrailPipeline";

// A saved-only check has no edit or enable path. Its original record is read
// without normalizing credentials, thresholds or provider-specific settings.
export default function StoredRuleDrawer({
  open,
  row,
  meta,
  canEdit,
  enforced,
  onClose,
  onTurnOff,
  onRemove,
}) {
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setPending(null);
    setBusy(false);
    setFailed(false);
  }, [open, row]);
  const label = row?.label || row?.id || "Check";
  const details =
    row?.id === "tool-permissions"
      ? storedToolSettings(row?.check)
      : storedSettings(meta || {}, row?.check);
  const confirm = async () => {
    if (!canEdit || busy || !pending) return;
    if (pending === "off" && !row?.enabled) return;
    setBusy(true);
    setFailed(false);
    try {
      const result = await (pending === "off" ? onTurnOff() : onRemove());
      if (result?.gateway_synced === false) {
        setFailed(true);
        return;
      }
      setPending(null);
      onClose();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <GatewayDetailDrawer
      open={open}
      onClose={busy ? () => {} : onClose}
      title={label}
      subtitle="Read-only saved check"
      width={480}
    >
      <Stack spacing={2} data-stored-only-check={row?.id}>
        <Typography color="text.secondary">
          {STORED_ONLY_OVERVIEW_HELP}
        </Typography>
        {!enforced && <Alert severity="warning">{GUARDRAILS_OFF_BANNER}</Alert>}
        <GatewayStatusChip
          label={!enforced ? "Not enforced" : row?.enabled ? "On" : "Off"}
          tone={enforced && row?.enabled ? "healthy" : "neutral"}
          showIcon={false}
        />
        <Typography>Action: {row?.action}</Typography>
        <Typography>
          Threshold: {Number(row?.threshold ?? 0).toFixed(2)}
        </Typography>
        {details.map((text) => (
          <Typography
            key={text}
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {text}
          </Typography>
        ))}
        {Number(row?.threshold) >= 1 && (
          <Alert severity="warning">{NEVER_TRIGGERS_WARNING}</Alert>
        )}
        {failed && (
          <Alert severity="warning">
            The change could not be confirmed on the gateway. Check its status
            before retrying.
          </Alert>
        )}
        <Stack direction="row" spacing={1}>
          {row?.enabled && (
            <GatewayAdminOnly allowed={canEdit}>
              <Button
                disabled={!canEdit || busy}
                onClick={() => canEdit && setPending("off")}
              >
                Turn off
              </Button>
            </GatewayAdminOnly>
          )}
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              color="error"
              disabled={!canEdit || busy}
              onClick={() => canEdit && setPending("remove")}
            >
              Remove
            </Button>
          </GatewayAdminOnly>
          <Button disabled={busy} onClick={onClose}>
            Close
          </Button>
        </Stack>
      </Stack>
      <ConfirmDialog
        open={Boolean(pending)}
        title={pending === "off" ? "Turn off check" : "Remove check"}
        message={
          pending === "off"
            ? storedOnlyOffMessage(label)
            : `Remove "${label}" from this gateway?`
        }
        confirmLabel={pending === "off" ? "Turn off" : "Remove"}
        onClose={() => !busy && setPending(null)}
        onConfirm={confirm}
      />
    </GatewayDetailDrawer>
  );
}
StoredRuleDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  row: PropTypes.object,
  meta: PropTypes.object,
  canEdit: PropTypes.bool,
  enforced: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onTurnOff: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};
