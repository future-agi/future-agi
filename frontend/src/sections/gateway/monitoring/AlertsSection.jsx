import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";

import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import { useGatewayContext } from "../context/useGatewayContext";
import {
  useGatewayConfig,
  useUpdateConfig,
} from "../providers/hooks/useGatewayConfig";
import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../components/frame";
import { GatewayIcon } from "../shell/gateway-icons";
import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { GATEWAY_ADMIN_ONLY, useGatewayAdmin } from "../hooks/useGatewayAdmin";
import {
  DEFAULT_ALERT_COOLDOWN,
  alertRuleStatus,
  effectiveAlertSeverity,
} from "../constants/alerting";
import CreateAlertRuleDialog from "./CreateAlertRuleDialog";
import CreateChannelDialog from "./CreateChannelDialog";
import {
  channelTypeLabel,
  describeRule,
  extractAlertRules,
  extractChannels,
  isOn,
  maskUrl,
  reportAlertingSaved,
} from "./alertingModel";
import { EMPTY } from "../utils/formatters";

// Alerts: the rules the gateway evaluates and the channels it notifies, as
// two tabs of one page (/monitoring/rules, /monitoring/channels). Each rule
// shows what the gateway applies: its severity (warning when none is
// stored), its cooldown (the gateway's 15m when none is stored), and a
// status read from what the gateway reports: Active only when it says it
// enforces org alerting. A channel's stored severity filter is not pushed,
// so it is not shown. Rules and channels are the org's gateway config:
// only an Owner or Admin can add or delete one.

// The status chip's colours as gateway tones.
const STATUS_TONES = {
  success: "healthy",
  warning: "warning",
  default: "neutral",
};

const SEVERITY_TONES = {
  critical: "error",
  warning: "warning",
  info: "neutral",
};

const TABS = [
  { slug: "rules", label: "Rules" },
  { slug: "channels", label: "Channels" },
];

function Clipped({ value, secondary }) {
  return (
    <Box
      component="span"
      title={typeof value === "string" ? value : undefined}
      sx={{
        display: "block",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        ...(secondary && { color: "text.secondary" }),
      }}
    >
      {value === "" || value == null ? EMPTY : value}
    </Box>
  );
}

Clipped.propTypes = { value: PropTypes.node, secondary: PropTypes.bool };

// Below Admin the button is disabled, and its tooltip (on the wrapping span)
// says why.
function DeleteButton({ label, onClick, disabled, canEdit }) {
  return (
    <Tooltip title={canEdit ? label : GATEWAY_ADMIN_ONLY}>
      <span>
        <IconButton
          size="small"
          aria-label={label}
          onClick={onClick}
          disabled={disabled || !canEdit}
        >
          <GatewayIcon name="trash" size={16} />
        </IconButton>
      </span>
    </Tooltip>
  );
}

DeleteButton.propTypes = {
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  canEdit: PropTypes.bool.isRequired,
};

export default function AlertsSection({ tab = "rules" }) {
  const navigate = useNavigate();
  const active = TABS.some((t) => t.slug === tab) ? tab : "rules";
  const { gatewayId, isLoading: gatewayLoading } = useGatewayContext();
  const {
    data: config,
    isLoading: configLoading,
    error: configError,
    refetch,
  } = useGatewayConfig(gatewayId, { meta: { errorHandled: true } });
  const updateConfig = useUpdateConfig();
  const [createOpen, setCreateOpen] = useState(false);
  // The rule or channel a row's Delete asked to delete, until the user
  // confirms: { kind: "rule" | "channel", item, name }.
  const [deleteTarget, setDeleteTarget] = useState(null);
  const { canEdit } = useGatewayAdmin();

  const rules = useMemo(() => extractAlertRules(config), [config]);
  const alertingEnabled = (config?.alerting || config?.alerts)?.enabled;
  const sectionsEnforced = config?.org_sections_enforced;
  const channels = useMemo(() => extractChannels(config), [config]);
  const loading = gatewayLoading || configLoading;

  const save = useCallback(
    (alerting, successMessage, failureMessage) =>
      updateConfig.mutate(
        { gatewayId, config: { alerting } },
        {
          onSuccess: (result) => reportAlertingSaved(result, successMessage),
          onError: () => enqueueSnackbar(failureMessage, { variant: "error" }),
        },
      ),
    [gatewayId, updateConfig],
  );

  // The patch endpoint replaces a list it is given, so deleting is "save the
  // list without this one", whichever shape the config was stored in.
  const deleteRule = useCallback(
    (index) => {
      const rule = rules[index];
      save(
        { rules: rules.filter((_, i) => i !== index) },
        `Alert rule "${rule?.name || index + 1}" deleted`,
        "The alert rule could not be deleted",
      );
    },
    [rules, save],
  );

  const deleteChannel = useCallback(
    (index) => {
      const channel = channels[index];
      save(
        { channels: channels.filter((_, i) => i !== index) },
        `Channel "${channel?.name || index + 1}" deleted`,
        "The channel could not be deleted",
      );
    },
    [channels, save],
  );

  // Each minimum holds its header: the label, 32 px of padding, the 24 px
  // menu button and, sortable, the 24 px sort arrow (monitoring/__tests__).
  const ruleColumns = useMemo(
    () => [
      {
        colId: "name",
        headerName: "Rule",
        minWidth: 170,
        flex: 1.2,
        valueGetter: ({ data }) => data.name,
        cellRenderer: ({ value }) => <Clipped value={value} />,
      },
      {
        colId: "when",
        headerName: "Fires when",
        minWidth: 220,
        flex: 1.8,
        sortable: false,
        valueGetter: ({ data }) => describeRule(data),
        cellRenderer: ({ value }) => <Clipped value={value} />,
      },
      {
        colId: "cooldown",
        headerName: "Cooldown",
        minWidth: 150,
        flex: 0.6,
        valueGetter: ({ data }) =>
          data.cooldown || `${DEFAULT_ALERT_COOLDOWN} (default)`,
        cellRenderer: ({ value, data }) => (
          <Clipped value={value} secondary={!data.cooldown} />
        ),
      },
      {
        colId: "severity",
        headerName: "Severity",
        minWidth: 120,
        flex: 0.5,
        valueGetter: ({ data }) => effectiveAlertSeverity(data.severity),
        cellRenderer: ({ value }) => (
          <GatewayStatusChip
            tone={SEVERITY_TONES[value] || "neutral"}
            label={value}
            showIcon={false}
          />
        ),
      },
      {
        colId: "channels",
        headerName: "Notifies",
        minWidth: 160,
        flex: 1.2,
        sortable: false,
        valueGetter: ({ data }) =>
          Array.isArray(data.channels) ? data.channels.join(", ") : "",
        cellRenderer: ({ value }) => (
          <Clipped value={value || "Nobody"} secondary={!value} />
        ),
      },
      {
        colId: "status",
        headerName: "Status",
        // Holds its longest label, "Not enforced on this gateway".
        minWidth: 240,
        flex: 1,
        valueGetter: ({ data }) =>
          alertRuleStatus({ rule: data, alertingEnabled, sectionsEnforced })
            .label,
        cellRenderer: ({ data }) => {
          const status = alertRuleStatus({
            rule: data,
            alertingEnabled,
            sectionsEnforced,
          });
          return (
            <Tooltip title={status.tooltip}>
              <span>
                <GatewayStatusChip
                  tone={STATUS_TONES[status.color] || "neutral"}
                  label={status.label}
                  showIcon={false}
                />
              </span>
            </Tooltip>
          );
        },
      },
      {
        colId: "actions",
        headerName: "",
        width: 64,
        minWidth: 64,
        maxWidth: 64,
        flex: 0,
        sortable: false,
        resizable: false,
        valueGetter: ({ data }) => data.name,
        cellRenderer: ({ data }) => (
          <DeleteButton
            label={`Delete rule ${data.name}`}
            disabled={updateConfig.isPending}
            canEdit={canEdit}
            onClick={() =>
              setDeleteTarget({
                kind: "rule",
                item: data,
                name: data.name,
              })
            }
          />
        ),
      },
    ],
    [alertingEnabled, canEdit, rules, sectionsEnforced, updateConfig.isPending],
  );

  const channelColumns = useMemo(
    () => [
      {
        colId: "name",
        headerName: "Channel",
        minWidth: 160,
        flex: 1.2,
        valueGetter: ({ data }) => data.name,
        cellRenderer: ({ value }) => <Clipped value={value} />,
      },
      {
        colId: "type",
        headerName: "Type",
        minWidth: 116,
        flex: 0.6,
        valueGetter: ({ data }) => channelTypeLabel(data.type),
      },
      {
        colId: "url",
        headerName: "Endpoint",
        minWidth: 200,
        flex: 1.6,
        sortable: false,
        valueGetter: ({ data }) =>
          data.type === "log" ? "Gateway log" : maskUrl(data.url),
        cellRenderer: ({ value }) => <Clipped value={value} secondary />,
      },
      {
        colId: "used",
        headerName: "Used by",
        minWidth: 136,
        flex: 0.6,
        valueGetter: ({ data }) =>
          rules.filter(
            (r) => Array.isArray(r.channels) && r.channels.includes(data.name),
          ).length,
        valueFormatter: ({ value }) =>
          value === 1 ? "1 rule" : `${value} rules`,
      },
      {
        colId: "state",
        headerName: "State",
        minWidth: 116,
        flex: 0.5,
        valueGetter: ({ data }) => (isOn(data) ? "On" : "Off"),
        cellRenderer: ({ value }) => (
          <GatewayStatusChip
            tone={value === "On" ? "healthy" : "neutral"}
            label={value}
            showIcon={false}
          />
        ),
      },
      {
        colId: "actions",
        headerName: "",
        width: 64,
        minWidth: 64,
        maxWidth: 64,
        flex: 0,
        sortable: false,
        resizable: false,
        valueGetter: ({ data }) => data.name,
        cellRenderer: ({ data }) => (
          <DeleteButton
            label={`Delete channel ${data.name}`}
            disabled={updateConfig.isPending}
            canEdit={canEdit}
            onClick={() =>
              setDeleteTarget({
                kind: "channel",
                item: data,
                name: data.name,
              })
            }
          />
        ),
      },
    ],
    [canEdit, channels, rules, updateConfig.isPending],
  );

  const onTab = (_event, slug) =>
    navigate(`${paths.dashboard.gateway.monitoring}/${slug}`, {
      replace: true,
    });

  const isRules = active === "rules";

  return (
    <GatewayPageFrame
      title="Alerts"
      description="Rules the gateway checks against its own traffic, and where it sends a notification."
      actions={
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            variant="contained"
            color="primary"
            startIcon={<GatewayIcon name="plus" size={16} />}
            onClick={() => setCreateOpen(true)}
            disabled={loading || Boolean(configError) || !canEdit}
          >
            {isRules ? "Create rule" : "Add channel"}
          </Button>
        </GatewayAdminOnly>
      }
    >
      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <CustomTabs value={active} onChange={onTab} aria-label="Alerts views">
          {TABS.map((t) => (
            <Tab
              key={t.slug}
              value={t.slug}
              label={`${t.label} ${t.slug === "rules" ? rules.length : channels.length}`}
            />
          ))}
        </CustomTabs>
      </Box>

      <Typography typography="s2" color="text.secondary">
        A rule runs only on a gateway that enforces org alerting; each
        rule&apos;s status says what this gateway reports. After a rule fires it
        waits its cooldown ({DEFAULT_ALERT_COOLDOWN} when none is set) before it
        can fire again; a 0s cooldown fires on every request past its threshold.
      </Typography>

      {loading ? (
        <Stack spacing={1} aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} variant="rounded" height={40} />
          ))}
        </Stack>
      ) : configError && !config ? (
        <GatewayEmptyState
          icon="alerts"
          title="Couldn't load your alert rules"
          description={
            configError.message || "The gateway config request failed."
          }
          actionLabel="Retry"
          onAction={() => refetch?.()}
        />
      ) : (
        <GatewayGrid
          key={active}
          rowData={isRules ? rules : channels}
          columnDefs={isRules ? ruleColumns : channelColumns}
          stateKey={isRules ? "gateway-alert-rules" : "gateway-alert-channels"}
          getRowId={(params) => String(params.data.name)}
          rowHeight={44}
          headerHeight={36}
          height={Math.min(
            520,
            40 + Math.max((isRules ? rules : channels).length, 3) * 44,
          )}
          emptyState={
            isRules
              ? {
                  icon: "alerts",
                  title: "No alert rules yet",
                  description: channels.length
                    ? "Create a rule to be told when errors, latency, cost or traffic cross a threshold."
                    : "Add a channel first, then create a rule that notifies it.",
                  actionLabel: "Create rule",
                  onAction: canEdit ? () => setCreateOpen(true) : undefined,
                }
              : {
                  icon: "webhooks",
                  title: "No channels yet",
                  description:
                    "A channel is where a rule sends its notification: a webhook, Slack, or the gateway log.",
                  actionLabel: "Add channel",
                  onAction: canEdit ? () => setCreateOpen(true) : undefined,
                }
          }
        />
      )}

      <CreateAlertRuleDialog
        open={isRules && createOpen}
        onClose={() => setCreateOpen(false)}
        gatewayId={gatewayId}
        existingRules={rules}
        channelNames={channels.map((c) => c?.name).filter(Boolean)}
      />
      <CreateChannelDialog
        open={!isRules && createOpen}
        onClose={() => setCreateOpen(false)}
        gatewayId={gatewayId}
        existingChannels={channels}
      />
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        title={
          deleteTarget
            ? `Delete ${deleteTarget.kind} ${deleteTarget.name}?`
            : "Delete"
        }
        message={
          deleteTarget?.kind === "channel"
            ? "Rules that notify this channel no longer send to it. This cannot be undone."
            : "The gateway no longer checks this rule or sends its notifications. This cannot be undone."
        }
        confirmLabel={
          deleteTarget?.kind === "channel" ? "Delete channel" : "Delete rule"
        }
        confirmColor="error"
        isLoading={updateConfig.isPending}
        onConfirm={() => {
          if (!canEdit || !deleteTarget) return;
          const target = deleteTarget;
          const index = (target.kind === "channel" ? channels : rules).indexOf(
            target.item,
          );
          setDeleteTarget(null);
          // A refreshed/replaced item needs a fresh named confirmation.
          if (index < 0) return;
          if (target.kind === "channel") deleteChannel(index);
          else deleteRule(index);
        }}
      />
    </GatewayPageFrame>
  );
}

AlertsSection.propTypes = { tab: PropTypes.string };
