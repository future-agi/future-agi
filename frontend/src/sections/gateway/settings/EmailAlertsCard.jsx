import EmptyState from "src/components/empty-state";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  CircularProgress,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import {
  useEmailAlerts,
  useUpdateEmailAlert,
  useDeleteEmailAlert,
} from "./hooks/useEmailAlerts";
import EmailAlertDialog from "./EmailAlertDialog";

export default function EmailAlertsCard() {
  const { data: alerts = [], isLoading } = useEmailAlerts();
  const { mutate: updateAlert } = useUpdateEmailAlert();
  const { mutate: deleteAlert } = useDeleteEmailAlert();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingAlert, setEditingAlert] = useState(null);

  const handleAdd = () => {
    setEditingAlert(null);
    setDialogOpen(true);
  };

  const handleEdit = (alert) => {
    setEditingAlert(alert);
    setDialogOpen(true);
  };

  const handleToggle = (alert) => {
    updateAlert({ id: alert.id, is_active: !alert.is_active });
  };

  const handleDelete = (alert) => {
    deleteAlert(alert.id);
  };

  return (
    <>
      <Section variant="panel">
        <Box
          display="flex"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Typography variant="h6" fontSize={12}>
            Email Alerts
          </Typography>
          <Button
            size="small"
            variant="contained"
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={handleAdd}
          >
            Add Alert
          </Button>
        </Box>

        {isLoading && <CircularProgress size={20} />}

        {!isLoading && alerts.length === 0 && (
          <EmptyState
            icon="solar:letter-linear"
            title="No email alerts yet"
            description="Get notified about budget overages, errors, guardrail triggers, and more."
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={handleAdd}
              >
                Add alert
              </Button>
            }
            sx={{ pt: 6, pb: 6 }}
          />
        )}

        {!isLoading && alerts.length > 0 && (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Recipients</TableCell>
                  <TableCell>Events</TableCell>
                  <TableCell>Provider</TableCell>
                  <TableCell align="center">Active</TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    Actions
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {alerts.map((alert) => (
                  <TableRow key={alert.id} hover>
                    <TableCell>
                      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                        {alert.name}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {(alert.recipients || []).slice(0, 2).join(" \u00B7 ")}
                        {(alert.recipients || []).length > 2 &&
                          ` \u00B7 +${alert.recipients.length - 2}`}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography
                        sx={{ typography: "s2", color: "text.secondary" }}
                      >
                        {(alert.events || []).length} event
                        {(alert.events || []).length !== 1 ? "s" : ""}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {alert.provider}
                      </Typography>
                    </TableCell>
                    <TableCell align="center">
                      <Switch
                        size="small"
                        checked={alert.is_active !== false}
                        onChange={() => handleToggle(alert)}
                      />
                    </TableCell>
                    <TableCell
                      sx={{ fontVariantNumeric: "tabular-nums" }}
                      align="right"
                    >
                      <Tooltip title="Edit">
                        <IconButton
                          className="row-hover-action"
                          size="small"
                          onClick={() => handleEdit(alert)}
                        >
                          <Iconify icon="solar:pen-linear" width={20} />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton
                          className="row-hover-action"
                          size="small"
                          color="error"
                          onClick={() => handleDelete(alert)}
                        >
                          <Iconify
                            icon="solar:trash-bin-trash-linear"
                            width={20}
                          />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Section>

      <EmailAlertDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        alert={editingAlert}
      />
    </>
  );
}

// No props needed — self-contained component
