import { useState } from "react";
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  IconButton,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { fDateTime } from "src/utils/format-time";
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
      <Card variant="outlined">
        <CardContent>
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
            mb={2}
          >
            <Typography variant="h6" fontSize={14}>
              Email Alerts
            </Typography>
            <Button
              size="small"
              variant="contained"
              startIcon={<Iconify icon="mdi:plus" />}
              onClick={handleAdd}
              sx={{ fontWeight: 500, textTransform: "none" }}
            >
              Add Alert
            </Button>
          </Box>

          {isLoading && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Loading...
            </Typography>
          )}

          {!isLoading && alerts.length === 0 && (
            <Stack alignItems="center" spacing={1} py={3}>
              <Iconify
                icon="mdi:email-outline"
                width={40}
                sx={{ color: "text.disabled" }}
              />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                No email alerts configured
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.disabled",
                  textAlign: "center",
                  maxWidth: 320,
                }}
              >
                Set up email alerts to get notified about budget overages,
                errors, guardrail triggers, and more.
              </Typography>
            </Stack>
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
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {alerts.map((alert) => (
                    <TableRow key={alert.id} hover>
                      <TableCell>
                        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                          {alert.name}
                        </Typography>
                        <Typography
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {alert.last_triggered_at
                            ? `Last sent ${fDateTime(alert.last_triggered_at)}`
                            : "Never sent"}
                        </Typography>
                        {alert.last_error && (
                          <Typography
                            sx={{ typography: "s3", color: "error.main" }}
                          >
                            {`Last delivery failed${
                              alert.last_error_at
                                ? ` ${fDateTime(alert.last_error_at)}`
                                : ""
                            }: ${alert.last_error}`}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Stack
                          direction="row"
                          spacing={0.5}
                          flexWrap="wrap"
                          useFlexGap
                        >
                          {(alert.recipients || []).slice(0, 2).map((r) => (
                            <Chip
                              key={r}
                              label={r}
                              size="small"
                              variant="outlined"
                            />
                          ))}
                          {(alert.recipients || []).length > 2 && (
                            <Chip
                              label={`+${alert.recipients.length - 2}`}
                              size="small"
                              variant="outlined"
                            />
                          )}
                        </Stack>
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
                        <Chip
                          label={alert.provider}
                          size="small"
                          color="default"
                        />
                      </TableCell>
                      <TableCell align="center">
                        <Switch
                          size="small"
                          checked={alert.is_active !== false}
                          onChange={() => handleToggle(alert)}
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip title="Edit">
                          <IconButton
                            size="small"
                            onClick={() => handleEdit(alert)}
                          >
                            <Iconify icon="mdi:pencil-outline" width={18} />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete">
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => handleDelete(alert)}
                          >
                            <Iconify icon="mdi:delete-outline" width={18} />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>

      <EmailAlertDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        alert={editingAlert}
      />
    </>
  );
}

// No props needed — self-contained component
