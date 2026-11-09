import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { fDateTime } from "src/utils/format-time";

import { GatewayEmptyState, GatewayGrid } from "../components/frame";
import EmailAlertDialog from "./EmailAlertDialog";
import {
  useDeleteEmailAlert,
  useEmailAlerts,
  useTestEmailAlert,
  useUpdateEmailAlert,
} from "./hooks/useEmailAlerts";
import { Glyph, SettingsPanel } from "./ui";

const PROVIDER_LABEL = { sendgrid: "SendGrid", resend: "Resend", smtp: "SMTP" };

// Two lines under the name: when it was last sent, and a failed delivery.
const ROW_HEIGHT = 64;

// AG Grid cells are block boxes; custom cells centre their content.
const Cell = ({ children }) => (
  <Box sx={{ display: "flex", alignItems: "center", height: 1, minWidth: 0 }}>
    {children}
  </Box>
);
Cell.propTypes = { children: PropTypes.node };

// The alert's name, when it was last sent and its last failed delivery.
function NameCell({ data }) {
  return (
    <Stack justifyContent="center" sx={{ height: 1, minWidth: 0 }}>
      <Typography typography="s2" fontWeight="fontWeightMedium" noWrap>
        {data.name}
      </Typography>
      <Typography typography="s3" color="text.secondary" noWrap>
        {data.last_triggered_at
          ? `Last sent ${fDateTime(data.last_triggered_at)}`
          : "Never sent"}
      </Typography>
      {data.last_error && (
        <Typography
          typography="s3"
          color="error.main"
          noWrap
          title={data.last_error}
        >
          {`Last delivery failed${
            data.last_error_at ? ` ${fDateTime(data.last_error_at)}` : ""
          }: ${data.last_error}`}
        </Typography>
      )}
    </Stack>
  );
}
NameCell.propTypes = { data: PropTypes.object };

function RecipientsCell({ value }) {
  const list = value || [];
  if (!list.length) {
    return (
      <Cell>
        <Typography typography="s2" color="text.disabled">
          —
        </Typography>
      </Cell>
    );
  }
  const shown = list.slice(0, 2);
  return (
    <Stack
      direction="row"
      gap={0.5}
      alignItems="center"
      sx={{ height: 1, minWidth: 0 }}
      title={list.join(", ")}
    >
      {shown.map((email) => (
        <Box
          key={email}
          component="span"
          sx={{
            px: 0.75,
            py: 0.125,
            borderRadius: 0.5,
            border: 1,
            borderColor: "divider",
            typography: "s3",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {email}
        </Box>
      ))}
      {list.length > shown.length && (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ flexShrink: 0 }}
        >
          +{list.length - shown.length}
        </Typography>
      )}
    </Stack>
  );
}
RecipientsCell.propTypes = { value: PropTypes.array };

// Turns the alert on or off: an alert that is off sends no email.
function ActiveCell({ data, context }) {
  return (
    <Cell>
      <Switch
        size="small"
        checked={data.is_active !== false}
        onChange={() => context.onToggle(data)}
        inputProps={{ "aria-label": `${data.name} active` }}
      />
    </Cell>
  );
}
ActiveCell.propTypes = { data: PropTypes.object, context: PropTypes.object };

// Send test delivers one email to the alert's recipients through its stored
// provider now, without spending its cooldown; Edit and Delete change it.
function RowActions({ data, context }) {
  return (
    <Stack direction="row" gap={0.5} alignItems="center" sx={{ height: 1 }}>
      <Button
        size="small"
        variant="outlined"
        onClick={() => context.onTest(data)}
        disabled={context.testing}
        aria-label={`Send a test email for ${data.name}`}
        sx={{ whiteSpace: "nowrap" }}
      >
        Send test
      </Button>
      <Tooltip title="Edit">
        <IconButton
          size="small"
          aria-label={`Edit ${data.name}`}
          onClick={() => context.onEdit(data)}
        >
          <Glyph name="edit" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Delete">
        <IconButton
          size="small"
          aria-label={`Delete ${data.name}`}
          onClick={() => context.onDelete(data)}
        >
          <Glyph name="delete" />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
RowActions.propTypes = { data: PropTypes.object, context: PropTypes.object };

const COLUMNS = [
  {
    field: "name",
    headerName: "Name",
    minWidth: 200,
    flex: 1.4,
    cellRenderer: NameCell,
  },
  {
    field: "recipients",
    headerName: "Recipients",
    minWidth: 220,
    flex: 1.6,
    sortable: false,
    cellRenderer: RecipientsCell,
  },
  {
    field: "events",
    headerName: "Events",
    minWidth: 124,
    flex: 0.6,
    valueGetter: ({ data }) => (data?.events || []).length,
    valueFormatter: ({ value }) => `${value} event${value === 1 ? "" : "s"}`,
    tooltipValueGetter: ({ data }) => (data?.events || []).join(", ") || null,
  },
  {
    field: "provider",
    headerName: "Provider",
    minWidth: 134,
    flex: 0.6,
    valueFormatter: ({ value }) => PROVIDER_LABEL[value] || value || "—",
  },
  {
    field: "is_active",
    headerName: "Active",
    minWidth: 110,
    flex: 0.4,
    cellRenderer: ActiveCell,
  },
  {
    colId: "actions",
    headerName: "",
    minWidth: 200,
    flex: 0.8,
    sortable: false,
    resizable: false,
    cellRenderer: RowActions,
  },
];

// Email alerts on Gateway settings > General: the gateway sends an alert's
// events to its recipients through its email provider, at most once per
// cooldown. Add one, switch it on or off, edit it, send a test or delete it.
export default function EmailAlertsCard() {
  const { data: alerts = [], isLoading } = useEmailAlerts();
  const { mutate: updateAlert } = useUpdateEmailAlert();
  const { mutate: deleteAlert } = useDeleteEmailAlert();
  const { mutate: testAlert, isPending: testing } = useTestEmailAlert();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingAlert, setEditingAlert] = useState(null);

  const openDialog = (alert) => {
    setEditingAlert(alert);
    setDialogOpen(true);
  };

  const sendTest = (alert) =>
    testAlert(
      { id: alert.id },
      {
        onSuccess: (data) =>
          enqueueSnackbar(
            data?.result?.message || data?.message || "Test email sent",
            { variant: data?.status === false ? "error" : "success" },
          ),
        onError: (err) =>
          enqueueSnackbar(
            getGatewayAuthenticationErrorMessage(err) ||
              err?.message ||
              err?.response?.data?.message ||
              "The test email failed",
            { variant: "error" },
          ),
      },
    );

  return (
    <>
      <SettingsPanel
        title="Email alerts"
        description="Emails your team gets when budgets, errors, guardrails or thresholds fire on this gateway."
        testId="gateway-email-alerts"
        actions={
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Glyph name="add" />}
            onClick={() => openDialog(null)}
          >
            Add alert
          </Button>
        }
      >
        {isLoading && (
          <Typography typography="s2" color="text.secondary">
            Loading...
          </Typography>
        )}
        {!isLoading && alerts.length === 0 && (
          <GatewayEmptyState
            icon="settings"
            title="No email alerts configured"
            description="Set up email alerts to get notified about budget overages, errors, guardrail triggers, and more."
          />
        )}
        {!isLoading && alerts.length > 0 && (
          <GatewayGrid
            rowData={alerts}
            columnDefs={COLUMNS}
            stateKey="gateway-email-alerts"
            getRowId={(params) => String(params.data.id)}
            height={alerts.length * ROW_HEIGHT + 42}
            rowHeight={ROW_HEIGHT}
            tooltipShowDelay={300}
            context={{
              onTest: sendTest,
              testing,
              onEdit: openDialog,
              onToggle: (alert) =>
                updateAlert({ id: alert.id, is_active: !alert.is_active }),
              onDelete: (alert) => deleteAlert(alert.id),
            }}
          />
        )}
      </SettingsPanel>

      <EmailAlertDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        alert={editingAlert}
      />
    </>
  );
}
